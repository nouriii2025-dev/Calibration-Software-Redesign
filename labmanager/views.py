from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import math
from .forms import *
from .models import *
from datetime import timedelta
import calendar


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
@user_passes_test(is_lab_head)
def job_edit(request, pk):
    job = get_object_or_404(Job, pk=pk)

    if request.method == "POST":
        job_form = JobForm(request.POST, instance=job)

        line_item_formset = JobLineItemFormSet(
            request.POST,
            instance=job,
            prefix="line_items",
        )

        doc_formset = JobDocumentFormSet(
            request.POST,
            request.FILES,
            instance=job,
            prefix="documents",
        )

        if (
            job_form.is_valid()
            and line_item_formset.is_valid()
            and doc_formset.is_valid()
        ):
            with transaction.atomic():

                # -------------------------------------------------
                # Save main Job information
                # -------------------------------------------------

                old_quantity = job.quantity

                job = job_form.save()

                new_quantity = job.quantity

                # -------------------------------------------------
                # Save line items
                # -------------------------------------------------

                line_items = line_item_formset.save()

                # -------------------------------------------------
                # Save documents
                # -------------------------------------------------

                doc_formset.save()

                # -------------------------------------------------
                # Handle certificate quantity changes
                # -------------------------------------------------

                current_certificate_count = job.certificates.count()

                # Quantity increased
                if new_quantity > current_certificate_count:

                    certificates_to_create = (
                        new_quantity - current_certificate_count
                    )

                    for _ in range(certificates_to_create):
                        Certificate.objects.create(job=job)

                # Quantity decreased
                elif new_quantity < current_certificate_count:

                    excess_count = (
                        current_certificate_count - new_quantity
                    )

                    # Only remove UNUSED certificates.
                    #
                    # Certificates that already have a line item
                    # or calibration data must not be deleted.
                    unused_certificates = list(
                        job.certificates
                        .filter(
                            line_item__isnull=True,
                        )
                        .order_by("-number")[:excess_count]
                    )

                    for certificate in unused_certificates:
                        certificate.delete()

                # -------------------------------------------------
                # Re-assign certificates according to line items
                # -------------------------------------------------

                # First detach certificates from line items that
                # were changed/deleted.
                #
                # IMPORTANT:
                # Existing certificate data is preserved.
                #

                certificates = list(
                    job.certificates
                    .select_related("line_item")
                    .order_by("number")
                )

                # Existing assignments are preserved where possible.
                #
                # Rebuild only the assignment pool for certificates
                # that are currently unassigned.
                #

                for line_item in line_items:

                    if not line_item.pk:
                        continue

                    # How many certificates already belong to this
                    # line item?
                    assigned_count = line_item.certificates.count()

                    required_count = line_item.quantity

                    # If this line item needs more certificates,
                    # assign available unassigned certificates.
                    if assigned_count < required_count:

                        certificates_needed = (
                            required_count - assigned_count
                        )

                        available = (
                            job.certificates
                            .filter(line_item__isnull=True)
                            .order_by("number")[:certificates_needed]
                        )

                        for certificate in available:
                            certificate.line_item = line_item
                            certificate.reference_procedure = (
                                line_item.reference_procedure
                            )
                            certificate.uuc_full_scale = (
                                line_item.range_to
                            )
                            certificate.uuc_unit = (
                                line_item.unit or ""
                            )

                            certificate.save(
                                update_fields=[
                                    "line_item",
                                    "reference_procedure",
                                    "uuc_full_scale",
                                    "uuc_unit",
                                ]
                            )

                    # If line item quantity was reduced, detach the
                    # extra certificates.
                    elif assigned_count > required_count:

                        excess = (
                            assigned_count - required_count
                        )

                        assigned_certificates = list(
                            line_item.certificates
                            .order_by("-number")[:excess]
                        )

                        for certificate in assigned_certificates:

                            # Do not destroy certificate information.
                            # Just make it available for reassignment.
                            certificate.line_item = None
                            certificate.save(
                                update_fields=["line_item"]
                            )

                # -------------------------------------------------
                # Re-run assignment for any remaining unassigned
                # certificates.
                # -------------------------------------------------

                for line_item in (
                    job.line_items
                    .all()
                    .order_by("id")
                ):

                    line_item.assign_certificates()

            messages.success(
                request,
                f"Job {job.job_number} updated successfully."
            )

            return redirect(
                "job_detail",
                pk=job.pk
            )

    else:
        job_form = JobForm(instance=job)

        line_item_formset = JobLineItemFormSet(
            instance=job,
            prefix="line_items",
        )

        doc_formset = JobDocumentFormSet(
            instance=job,
            prefix="documents",
        )

    return render(
        request,
        "labmanager/job_edit.html",
        {
            "job": job,
            "job_form": job_form,
            "line_item_formset": line_item_formset,
            "doc_formset": doc_formset,
        },
    )


# @login_required
# def job_detail(request, pk):
#     job = get_object_or_404(Job, pk=pk)
#     if not request.user.is_lab_head and not job.line_items.filter(assigned_to=request.user).exists():
#         messages.error(request, "You do not have access to that job.")
#         return redirect("dashboard")
#     return render(request, "labmanager/job_detail.html", {"job": job})
@login_required
def job_detail(request, pk):
    job = get_object_or_404(Job, pk=pk)

    # ---------------------------------------------------------
    # Lab Head can see the complete job.
    # Technician can only access a job where at least one
    # line item is assigned to them.
    # ---------------------------------------------------------
    if request.user.is_lab_head:
        visible_line_items = job.line_items.all()
        visible_certificates = job.certificates.all()

    else:
        # Only line items assigned to this technician
        visible_line_items = job.line_items.filter(
            assigned_to=request.user
        )

        # Only certificates belonging to those assigned line items
        visible_certificates = job.certificates.filter(
            line_item__assigned_to=request.user
        )

        if not visible_line_items.exists():
            messages.error(
                request,
                "You do not have access to that job."
            )
            return redirect("dashboard")

    return render(
        request,
        "labmanager/job_detail.html",
        {
            "job": job,
            "visible_line_items": visible_line_items,
            "visible_certificates": visible_certificates,
        },
    )


# ---------- Certificates ----------

def _can_edit_certificate(user, certificate):
    if user.is_lab_head:
        return True
    return bool(certificate.line_item and certificate.line_item.assigned_to_id == user.id)


def add_months(base_date, months):
    """
    Add a number of months to a date while keeping the day valid.

    Example:
    31 January + 1 month = 28 February
    31 January + 2 months = 31 March
    """
    month_index = base_date.month - 1 + months

    year = base_date.year + month_index // 12
    month = month_index % 12 + 1

    last_day = calendar.monthrange(year, month)[1]
    day = min(base_date.day, last_day)

    return base_date.replace(
        year=year,
        month=month,
        day=day,
    )


def calculate_re_calibration_date(certificate, calibration_date):
    """
    Calculate the re-calibration date using the calibration
    validity configured on the certificate's Job Line Item.
    """

    if not calibration_date:
        return None

    line_item = certificate.line_item

    if not line_item:
        return None

    value = line_item.calibration_validity_value
    unit = line_item.calibration_validity_unit

    if not value or not unit:
        return None

    if unit == "days":
        return calibration_date + timedelta(days=value)

    if unit == "months":
        return add_months(calibration_date, value)

    if unit == "years":
        return add_months(calibration_date, value * 12)

    return None


@login_required
def certificate_detail(request, pk):
    certificate = get_object_or_404(
        Certificate.objects.select_related("line_item"),
        pk=pk
    )

    if not _can_edit_certificate(request.user, certificate):
        messages.error(
            request,
            "You do not have access to that certificate."
        )
        return redirect("dashboard")

    ResultFormSet = get_calibration_result_formset(certificate)

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

            # =========================================================
            # SAVE CERTIFICATE
            # =========================================================

            certificate = cert_form.save(commit=False)

            # Automatically calculate re-calibration date
            # from the Job Line Item validity settings.
            if certificate.calibration_date:
                certificate.re_calibration_date = (
                    calculate_re_calibration_date(
                        certificate,
                        certificate.calibration_date,
                    )
                )
            else:
                certificate.re_calibration_date = None

            certificate.save()

            # =========================================================
            # SAVE CALIBRATION RESULT ROWS
            # =========================================================

            # Do NOT call result_formset.save() yet.
            # First calculate Mean and Deviation and then save
            # every result instance explicitly.

            result_instances = result_formset.save(commit=False)

            # ---------------------------------------------------------
            # Handle deleted rows
            # ---------------------------------------------------------

            for obj in result_formset.deleted_objects:
                obj.delete()

            # ---------------------------------------------------------
            # Calculate and save each result
            # ---------------------------------------------------------

            for result_form, result_instance in zip(
                result_formset.forms,
                result_instances
            ):

                # Skip rows marked for deletion
                if result_form.cleaned_data.get("DELETE"):
                    continue

                applied = result_form.cleaned_data.get(
                    "applied_value"
                )
                upward = result_form.cleaned_data.get(
                    "upward_reading"
                )
                downward = result_form.cleaned_data.get(
                    "downward_reading"
                )

                # -----------------------------------------------------
                # Normalize values
                # -----------------------------------------------------

                applied = (
                    str(applied).strip()
                    if applied is not None
                    else ""
                )

                upward = (
                    str(upward).strip()
                    if upward is not None
                    else ""
                )

                downward = (
                    str(downward).strip()
                    if downward is not None
                    else ""
                )

                # -----------------------------------------------------
                # Calculate Mean and Deviation only when all
                # three readings are available.
                # -----------------------------------------------------

                if applied and upward and downward:

                    try:
                        applied_decimal = Decimal(applied)
                        upward_decimal = Decimal(upward)
                        downward_decimal = Decimal(downward)

                        mean = (
                            upward_decimal + downward_decimal
                        ) / Decimal("2")

                        deviation = (
                            applied_decimal - mean
                        )

                        mean = mean.quantize(
                            Decimal("0.0001"),
                            rounding=ROUND_HALF_UP
                        )

                        deviation = deviation.quantize(
                            Decimal("0.0001"),
                            rounding=ROUND_HALF_UP
                        )

                        result_instance.mean_value = str(mean)
                        result_instance.deviation = str(deviation)

                    except (
                        InvalidOperation,
                        ValueError,
                        TypeError
                    ):
                        # If a value cannot be converted to Decimal,
                        # do not crash the whole certificate.
                        result_instance.mean_value = ""
                        result_instance.deviation = ""

                else:
                    # Incomplete rows are allowed.
                    # Keep Mean and Deviation empty until all
                    # three readings have been entered.
                    result_instance.mean_value = ""
                    result_instance.deviation = ""

                # -----------------------------------------------------
                # SAVE RESULT TO DATABASE
                # -----------------------------------------------------

                result_instance.save()

            # =========================================================
            # SAVE BUTTON ACTIONS
            # =========================================================

            # Calculate Uncertainty
            if "calculate_uncertainty" in request.POST:
                return redirect(
                    "uncertainty",
                    pk=certificate.pk
                )

            # Save and Print
            if "save_and_print" in request.POST:
                return redirect(
                    "certificate_print",
                    pk=certificate.pk
                )

            # Normal Save
            messages.success(
                request,
                f"Certificate {certificate.number} updated."
            )

            return redirect(
                "job_detail",
                pk=certificate.job_id
            )

        else:
            # ---------------------------------------------------------
            # Debug validation errors
            # ---------------------------------------------------------

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
    technicians = User.objects.filter(role=User.Role.TECHNICIAN, is_superuser=False)
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


# ================================================================
# PRESSURE UNIT CONVERSION
# ================================================================

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
    Convert a pressure value FROM the UUC unit TO bar.

    Used for CMC selection because the CMC table
    is defined in bar.
    """

    if value is None:
        return None

    try:
        value = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None

    unit_key = (unit or "bar").strip().lower()

    factor = UNIT_TO_BAR.get(unit_key)

    if factor is None:
        return None

    return value * factor


def convert_from_bar(value, unit):
    """
    Convert a pressure value FROM bar TO the UUC unit.

    Used for the Reference Instrument Range so that
    it is in the same unit as the calibration readings.
    """

    if value is None:
        return None

    try:
        value = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None

    unit_key = (unit or "bar").strip().lower()

    factor = UNIT_TO_BAR.get(unit_key)

    if factor is None or factor == 0:
        return None

    return value / factor

def get_cmc(applied_pressure, uuc_unit, pressure_media):

    applied_bar = convert_to_bar(
        applied_pressure,
        uuc_unit,
    )

    if applied_bar is None:
        return None

    media = (pressure_media or "").strip().lower()

    if media == "pneumatic":

        if Decimal("-0.95") <= applied_bar < Decimal("0"):
            return Decimal("0.10")

        if Decimal("0") <= applied_bar <= Decimal("30"):
            return Decimal("0.080")

        if Decimal("30") < applied_bar <= Decimal("100"):
            return Decimal("0.020")

    elif media == "hydraulic":

        if Decimal("0") <= applied_bar <= Decimal("0.6"):
            return Decimal("0.080")

        # 0.6 to 6 bar has no supplied CMC

        if Decimal("6") < applied_bar <= Decimal("60"):
            return Decimal("0.020")

        if Decimal("60") < applied_bar <= Decimal("1000"):
            return Decimal("0.025")

    return None



def calculate_uncertainty_for_result(
    result,
    certificate,
):
    """
    Calculate all uncertainty components for one calibration result.

    Important unit rules:

    - Applied pressure is in the UUC unit.
    - CMC selection uses applied pressure converted to bar.
    - Reference Instrument Range is stored in bar.
    - Reference Instrument Range is converted FROM bar TO UUC unit
      before calculating Std. Un. for Press Tx.
    """

    # m1 = result.upward_reading
    # m2 = result.downward_reading
    # applied = result.applied_value

    # if (
    #     m1 is None
    #     or m2 is None
    #     or applied is None
    # ):
    #     return None

    # m1 = Decimal(str(m1))
    # m2 = Decimal(str(m2))
    # applied = Decimal(str(applied))
    m1 = result.upward_reading
    m2 = result.downward_reading
    applied = result.applied_value

    # CalibrationResult fields are CharFields, therefore an empty
    # HTML input is stored as "" rather than None.
    m1 = str(m1).strip() if m1 is not None else ""
    m2 = str(m2).strip() if m2 is not None else ""
    applied = str(applied).strip() if applied is not None else ""

    # Do not calculate uncertainty for an empty or incomplete row.
    if not m1 or not m2 or not applied:
        return None

    try:
        m1 = Decimal(m1)
        m2 = Decimal(m2)
        applied = Decimal(applied)

    except (InvalidOperation, ValueError, TypeError):
        return None

    # ------------------------------------------------------------
    # Basic certificate values
    # ------------------------------------------------------------

    unit = certificate.uuc_unit or "bar"
    media = certificate.pressure_media or ""

    reference_range_bar = certificate.reference_instrument_range
    full_scale = certificate.uuc_full_scale

    assumed_resolution = certificate.assumed_resolution

    if reference_range_bar is None:
        return None

    if full_scale is None:
        return None

    if assumed_resolution is None:
        return None

    reference_range_bar = Decimal(str(reference_range_bar))
    full_scale = Decimal(str(full_scale))
    assumed_resolution = Decimal(str(assumed_resolution))

    # ------------------------------------------------------------
    # CMC
    #
    # Applied pressure:
    # UUC unit -> bar
    # ------------------------------------------------------------

    cmc = get_cmc(
        applied,
        unit,
        media,
    )

    if cmc is None:
        return None

    # ------------------------------------------------------------
    # Reference Instrument Range
    #
    # Stored in bar.
    #
    # Convert:
    #
    # bar -> UUC unit
    # ------------------------------------------------------------

    reference_range_uuc = convert_from_bar(
        reference_range_bar,
        unit,
    )

    if reference_range_uuc is None:
        return None

    # ------------------------------------------------------------
    # Repeatability
    #
    # Sample standard deviation of M1 and M2
    # ------------------------------------------------------------

    mean = (m1 + m2) / Decimal("2")

    variance = (
        ((m1 - mean) ** 2) +
        ((m2 - mean) ** 2)
    )

    variance = variance / Decimal("1")

    repeatability = variance.sqrt()

    # ------------------------------------------------------------
    # Hysteresis
    # ------------------------------------------------------------

    hysteresis = m2 - m1

    # ------------------------------------------------------------
    # Standard uncertainty due to hysteresis
    # ------------------------------------------------------------

    u_hysteresis = (
        hysteresis /
        (Decimal("2") * Decimal("3").sqrt())
    )

    # ------------------------------------------------------------
    # Standard uncertainty due to resolution
    # ------------------------------------------------------------

    u_resolution = (
        assumed_resolution /
        Decimal("3").sqrt()
    )

    # ------------------------------------------------------------
    # Standard uncertainty for Pressure Tx
    #
    # Reference range is now in the SAME unit as M1/M2.
    #
    # u = Reference Range × (CMC / 100) / 2
    # ------------------------------------------------------------

    u_pressure_tx = (
        reference_range_uuc *
        (cmc / Decimal("100")) /
        Decimal("2")
    )

    # ------------------------------------------------------------
    # Combined standard uncertainty
    # ------------------------------------------------------------

    combined = (
        (
            u_hysteresis ** 2
            +
            u_resolution ** 2
            +
            u_pressure_tx ** 2
        )
        .sqrt()
    )

    # ------------------------------------------------------------
    # Expanded uncertainty
    # k = 2
    # ------------------------------------------------------------

    expanded = combined * Decimal("2")

    # ------------------------------------------------------------
    # Expanded uncertainty % FSD
    # ------------------------------------------------------------

    if full_scale == 0:
        return None

    expanded_percent = (
        expanded /
        abs(full_scale)
    ) * Decimal("100")

    # ------------------------------------------------------------
    # Maximum expanded uncertainty
    # ------------------------------------------------------------

    maximum = max(
        expanded_percent,
        cmc,
    )

    return {
        "applied": applied,
        "m1": m1,
        "m2": m2,
        "repeatability": repeatability,
        "hysteresis": hysteresis,
        "u_hysteresis": u_hysteresis,
        "u_resolution": u_resolution,
        "reference_range_bar": reference_range_bar,
        "reference_range_uuc": reference_range_uuc,
        "u_pressure_tx": u_pressure_tx,
        "combined": combined,
        "expanded": expanded,
        "expanded_percent": expanded_percent,
        "cmc": cmc,
        "maximum": maximum,
    }


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
    # Inherit current Line Item range/unit
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

    # -------------------------------------------------------------
    # Calculate uncertainty completely in backend
    # -------------------------------------------------------------

    calculated_results = []

    for result in certificate.results.all():

        calculation = calculate_uncertainty_for_result(
            result,
            certificate,
        )

        calculated_results.append({
            "result": result,
            "calculation": calculation,
        })

    return render(
        request,
        "labmanager/uncertainty.html",
        {
            "certificate": certificate,
            "results": certificate.results.all(),
            "calculated_results": calculated_results,
            "assumed_resolution": certificate.assumed_resolution,
        },
    )


# @login_required
# def save_uncertainty(request, pk):
#     certificate = get_object_or_404(
#         Certificate.objects.prefetch_related("results"),
#         pk=pk,
#     )

#     if not _can_edit_certificate(request.user, certificate):
#         messages.error(
#             request,
#             "You do not have access to this certificate."
#         )
#         return redirect("dashboard")

#     if request.method != "POST":
#         return redirect("uncertainty", pk=certificate.pk)

#     # Receive calculated maximum uncertainty values from JavaScript
#     uncertainty_values = request.POST.getlist("uncertainty[]")

#     results = list(
#         certificate.results.all().order_by("id")
#     )

#     for result, value in zip(results, uncertainty_values):

#         value = (value or "").strip()

#         if value:
#             try:
#                 decimal_value = Decimal(value)

#                 result.uncertainty = str(
#                     decimal_value.quantize(
#                         Decimal("0.0001"),
#                         rounding=ROUND_HALF_UP
#                     )
#                 )

#             except Exception:
#                 result.uncertainty = ""
#         else:
#             result.uncertainty = ""

#         result.save(update_fields=["uncertainty"])

#     messages.success(
#         request,
#         "Expanded uncertainty values updated successfully."
#     )

#     return redirect(
#         "certificate_detail",
#         pk=certificate.pk
#     )
@login_required
def save_uncertainty(request, pk):

    certificate = get_object_or_404(
        Certificate.objects.select_related(
            "line_item",
        ).prefetch_related("results"),
        pk=pk,
    )

    if not _can_edit_certificate(request.user, certificate):
        messages.error(
            request,
            "You do not have access to this certificate."
        )
        return redirect("dashboard")

    if request.method != "POST":
        return redirect(
            "uncertainty",
            pk=certificate.pk
        )

    results = list(
        certificate.results.all().order_by("id")
    )

    saved_count = 0

    for result in results:

        calculation = calculate_uncertainty_for_result(
            result,
            certificate,
        )

        if calculation is None:
            result.uncertainty = ""

        else:
            maximum = calculation["maximum"]

            result.uncertainty = str(
                maximum.quantize(
                    Decimal("0.0001"),
                    rounding=ROUND_HALF_UP
                )
            )

            saved_count += 1

        result.save(
            update_fields=["uncertainty"]
        )

    messages.success(
        request,
        f"Expanded uncertainty values updated successfully."
    )

    return redirect(
        "certificate_detail",
        pk=certificate.pk
    )