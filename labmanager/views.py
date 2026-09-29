from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from decimal import Decimal, ROUND_HALF_UP
import math
from .forms import *
from .models import *


def is_lab_head(user):
    return user.is_authenticated and user.is_lab_head


# ---------- Auth ----------

def signup_view(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            return redirect("dashboard")
    else:
        form = SignUpForm()
    return render(request, "labmanager/signup.html", {"form": form})


class RoleAwareLoginView(LoginView):
    template_name = "labmanager/login.html"


# ---------- Dashboard ----------

@login_required
def dashboard(request):
    if request.user.is_lab_head:
        jobs = Job.objects.all()
    else:
        jobs = Job.objects.filter(line_items__assigned_to=request.user).distinct()

    # --- Filters (all optional, combine with AND) ---
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if search_query:
        jobs = jobs.filter(
            Q(job_number__icontains=search_query)
            | Q(customer_name__icontains=search_query)
            | Q(po_number__icontains=search_query)
        )

    if date_from:
        jobs = jobs.filter(committed_date__gte=date_from)
    if date_to:
        jobs = jobs.filter(committed_date__lte=date_to)

    jobs = jobs.distinct()

    # certificates_assigned_count is a Python property (not a DB field), so
    # assignment status is computed here rather than filtered in the ORM.
    jobs_list = list(jobs)
    complete_count = 0
    partial_count = 0
    unassigned_count = 0

    for job in jobs_list:
        assigned = job.certificates_assigned_count
        job.assignment_percent = int(round((assigned / job.quantity) * 100)) if job.quantity else 0
        if assigned <= 0:
            job.assignment_status = "unassigned"
            unassigned_count += 1
        elif assigned < job.quantity:
            job.assignment_status = "partial"
            partial_count += 1
        else:
            job.assignment_status = "complete"
            complete_count += 1

    if status_filter in ("complete", "partial", "unassigned"):
        jobs_list = [j for j in jobs_list if j.assignment_status == status_filter]

    context = {
        "jobs": jobs_list,
        "search_query": search_query,
        "status_filter": status_filter,
        "date_from": date_from,
        "date_to": date_to,
        "total_jobs": len(jobs_list) if not status_filter else (complete_count + partial_count + unassigned_count),
        "complete_count": complete_count,
        "partial_count": partial_count,
        "unassigned_count": unassigned_count,
    }
    return render(request, "labmanager/dashboard.html", context)


@login_required
@user_passes_test(is_lab_head)
def job_create(request):
    if request.method == "POST":
        job_form = JobForm(request.POST)
        # Validate against an unsaved placeholder instance first, so nothing
        # touches the database unless the whole form is valid.
        line_item_formset = JobLineItemFormSet(request.POST, instance=Job(), prefix="line_items")
        doc_formset = JobDocumentFormSet(request.POST, request.FILES, instance=Job(), prefix="documents")

        if job_form.is_valid() and line_item_formset.is_valid() and doc_formset.is_valid():
            with transaction.atomic():
                job = job_form.save(commit=False)
                job.created_by = request.user
                job.save()

                # Generate the fixed pool of certificates for this job's quantity.
                for _ in range(job.quantity):
                    Certificate.objects.create(job=job)

                line_item_formset.instance = job
                doc_formset.instance = job
                line_items = line_item_formset.save()
                for li in line_items:
                    li.assign_certificates()
                doc_formset.save()

            messages.success(request, f"Job {job.job_number} created with {job.quantity} certificates.")
            return redirect("job_detail", pk=job.pk)
    else:
        job_form = JobForm()
        line_item_formset = JobLineItemFormSet(prefix="line_items")
        doc_formset = JobDocumentFormSet(prefix="documents")

    next_job_number = Job.generate_job_number()
    return render(
        request,
        "labmanager/job_form.html",
        {
            "job_form": job_form,
            "line_item_formset": line_item_formset,
            "doc_formset": doc_formset,
            "next_job_number": next_job_number,
        },
    )


@login_required
def job_detail(request, pk):
    job = get_object_or_404(Job, pk=pk)
    if not request.user.is_lab_head and not job.line_items.filter(assigned_to=request.user).exists():
        messages.error(request, "You do not have access to that job.")
        return redirect("dashboard")
    return render(request, "labmanager/job_detail.html", {"job": job})


# ---------- Certificates ----------

def _can_edit_certificate(user, certificate):
    if user.is_lab_head:
        return True
    return bool(certificate.line_item and certificate.line_item.assigned_to_id == user.id)


@login_required
def certificate_detail(request, pk):
    certificate = get_object_or_404(
        Certificate.objects.select_related("line_item"),
        pk=pk
    )

    if not _can_edit_certificate(request.user, certificate):
        messages.error(request, "You do not have access to that certificate.")
        return redirect("dashboard")

    ResultFormSet = get_calibration_result_formset(certificate)

    # Always initialize the forms first.
    cert_form = CertificateForm(
        request.POST or None,
        instance=certificate
    )

    result_formset = ResultFormSet(
        request.POST or None,
        instance=certificate,
        prefix="results"
    )

    if request.method == "POST":

        if cert_form.is_valid() and result_formset.is_valid():

            # Save certificate
            cert_form.save()

            # Calculate Mean and Deviation before saving results
            for result_form in result_formset:
                if result_form.cleaned_data.get("DELETE"):
                    continue

                applied = result_form.cleaned_data.get("applied_value")
                upward = result_form.cleaned_data.get("upward_reading")
                downward = result_form.cleaned_data.get("downward_reading")

                if (
                    applied is not None
                    and upward is not None
                    and downward is not None
                ):
                    applied = Decimal(str(applied))
                    upward = Decimal(str(upward))
                    downward = Decimal(str(downward))

                    mean = (upward + downward) / Decimal("2")
                    deviation = applied - mean

                    mean = mean.quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP
                    )

                    deviation = deviation.quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP
                    )

                    result_form.instance.mean_value = mean
                    result_form.instance.deviation = deviation

            # Save calibration results
            result_formset.save()

            # Uncertainty Calculation button
            if "calculate_uncertainty" in request.POST:
                return redirect(
                    "uncertainty",
                    pk=certificate.pk
                )

            # Save and Print Certificate
            if "save_and_print" in request.POST:
                return redirect(
                    "certificate_print",
                    pk=certificate.pk
                )

            # Normal Save Certificate
            messages.success(
                request,
                f"Certificate {certificate.number} updated."
            )

            return redirect(
                "job_detail",
                pk=certificate.job_id
            )

        else:
            # Show validation errors in terminal while debugging
            print("CERTIFICATE FORM ERRORS:")
            print(cert_form.errors)

            print("RESULT FORMSET ERRORS:")
            print(result_formset.errors)

            print("RESULT FORMSET NON-FORM ERRORS:")
            print(result_formset.non_form_errors())

    return render(
        request,
        "labmanager/certificate_detail.html",
        {
            "certificate": certificate,
            "cert_form": cert_form,
            "result_formset": result_formset,
        },
    )


@login_required
def certificate_print(request, pk):
    certificate = get_object_or_404(
        Certificate.objects.select_related(
            "job",
            "line_item",
            "line_item__instrument",
        ).prefetch_related("results"),
        pk=pk,
    )

    if not _can_edit_certificate(request.user, certificate):
        messages.error(
            request,
            "You do not have access to that certificate."
        )
        return redirect("dashboard")

    return render(
        request,
        "labmanager/certificate_print.html",
        {
            "certificate": certificate,
            "results": certificate.results.all(),
        },
    )


# ---------- Instruments ----------

@login_required
@user_passes_test(is_lab_head)
def instrument_list(request):
    if request.method == "POST":
        form = InstrumentForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Instrument added.")
            return redirect("instrument_list")
    else:
        form = InstrumentForm()
    instruments = Instrument.objects.all()
    return render(request, "labmanager/instruments.html", {"form": form, "instruments": instruments})


@login_required
@user_passes_test(is_lab_head)
def instrument_toggle(request, pk):
    instrument = get_object_or_404(Instrument, pk=pk)
    instrument.is_active = not instrument.is_active
    instrument.save(update_fields=["is_active"])
    return redirect("instrument_list")


# ---------- Technicians ----------

@login_required
@user_passes_test(is_lab_head)
def technician_list(request):
    if request.method == "POST":
        form = TechnicianForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Technician added.")
            return redirect("technician_list")
    else:
        form = TechnicianForm()
    technicians = User.objects.filter(role=User.Role.TECHNICIAN)
    return render(request, "labmanager/technicians.html", {"form": form, "technicians": technicians})


@login_required
@user_passes_test(is_lab_head)
def technician_toggle(request, pk):
    technician = get_object_or_404(User, pk=pk, role=User.Role.TECHNICIAN)
    technician.is_active = not technician.is_active
    technician.save(update_fields=["is_active"])
    return redirect("technician_list")


# ================================================================
# UNCERTAINTY / CMC HELPERS
# ================================================================

CMC_TABLE = {
    "Pneumatic": [
        (Decimal("-0.95"), Decimal("0"), Decimal("0.10")),
        (Decimal("0"), Decimal("30"), Decimal("0.080")),
        (Decimal("30"), Decimal("100"), Decimal("0.020")),
    ],
    "Hydraulic": [
        (Decimal("0"), Decimal("0.6"), Decimal("0.080")),
        (Decimal("6"), Decimal("60"), Decimal("0.020")),
        (Decimal("60"), Decimal("1000"), Decimal("0.025")),
    ],
}


UNIT_TO_BAR = {
    "bar": Decimal("1"),
    "psi": Decimal("1") / Decimal("14.5038"),
    "kpa": Decimal("1") / Decimal("100"),
    "mpa": Decimal("10"),
    "kg/cm2": Decimal("1") / Decimal("1.01972"),
    "kgf/cm²": Decimal("1") / Decimal("1.01972"),
}


def convert_to_bar(value, unit):
    """
    Convert a pressure value from the UUC unit to bar.
    """

    if value is None:
        return None

    try:
        value = Decimal(str(value))
    except Exception:
        return None

    unit_key = (unit or "bar").strip().lower()

    factor = UNIT_TO_BAR.get(unit_key)

    if factor is None:
        return None

    return value * factor


def get_cmc(applied_pressure, uuc_unit, pressure_media):
    """
    Determine CMC percentage from the APPLIED PRESSURE.

    The CMC table is defined in bar, so the applied pressure
    is first converted to bar.
    """

    applied_bar = convert_to_bar(
        applied_pressure,
        uuc_unit,
    )

    if applied_bar is None:
        return None

    media = (pressure_media or "").strip()

    ranges = CMC_TABLE.get(media, [])

    for minimum, maximum, cmc in ranges:
        if minimum <= applied_bar <= maximum:
            return cmc

    return None


@login_required
def uncertainty(request, pk):

    certificate = get_object_or_404(
        Certificate.objects.select_related(
            "job",
            "line_item",
            "line_item__instrument",
        ).prefetch_related("results"),
        pk=pk,
    )

    if not _can_edit_certificate(request.user, certificate):
        messages.error(
            request,
            "You do not have access to this certificate."
        )
        return redirect("dashboard")

    # -------------------------------------------------------------
    # Make sure certificate inherits current Line Item range/unit
    # -------------------------------------------------------------

    if certificate.line_item:

        changed_fields = []

        line_item = certificate.line_item

        if (
            line_item.range_to is not None
            and certificate.uuc_full_scale != line_item.range_to
        ):
            certificate.uuc_full_scale = line_item.range_to
            changed_fields.append("uuc_full_scale")

        if (
            line_item.unit
            and certificate.uuc_unit != line_item.unit
        ):
            certificate.uuc_unit = line_item.unit
            changed_fields.append("uuc_unit")

        if changed_fields:
            certificate.save(update_fields=changed_fields)

    assumed_resolution = certificate.assumed_resolution


    return render(
        request,
        "labmanager/uncertainty.html",
        {
            "certificate": certificate,
            "results": certificate.results.all(),
            "assumed_resolution": assumed_resolution,
        },
    )


@login_required
def save_uncertainty(request, pk):
    certificate = get_object_or_404(
        Certificate.objects.prefetch_related("results"),
        pk=pk,
    )

    if not _can_edit_certificate(request.user, certificate):
        messages.error(
            request,
            "You do not have access to this certificate."
        )
        return redirect("dashboard")

    if request.method != "POST":
        return redirect("uncertainty", pk=certificate.pk)

    # Receive calculated maximum uncertainty values from JavaScript
    uncertainty_values = request.POST.getlist("uncertainty[]")

    results = list(
        certificate.results.all().order_by("id")
    )

    for result, value in zip(results, uncertainty_values):

        value = (value or "").strip()

        if value:
            try:
                decimal_value = Decimal(value)

                result.uncertainty = str(
                    decimal_value.quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP
                    )
                )

            except Exception:
                result.uncertainty = ""
        else:
            result.uncertainty = ""

        result.save(update_fields=["uncertainty"])

    messages.success(
        request,
        "Expanded uncertainty values updated successfully."
    )

    return redirect(
        "certificate_detail",
        pk=certificate.pk
    )
