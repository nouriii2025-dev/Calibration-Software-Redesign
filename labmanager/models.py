from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Max
from django.utils import timezone
from decimal import Decimal


class User(AbstractUser):
    """Custom user with a role. Lab Head has full access; Technician gets a
    restricted dashboard scoped to jobs assigned to them."""

    class Role(models.TextChoices):
        LAB_HEAD = "head", "Lab Head"
        TECHNICIAN = "technician", "Lab Technician"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.TECHNICIAN)

    @property
    def is_lab_head(self):
        return self.role == self.Role.LAB_HEAD

    @property
    def is_technician(self):
        return self.role == self.Role.TECHNICIAN

    def __str__(self):
        return self.get_full_name() or self.username


class Instrument(models.Model):
    """Master list of instruments, managed on a separate admin page and used
    to populate the dropdown on the job form."""

    name = models.CharField(max_length=150, unique=True)
    default_model = models.CharField(max_length=150, blank=True)
    default_range = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Job(models.Model):
    """A single calibration job created by a Lab Head. job_number and the
    pool of certificates are generated automatically from `quantity`."""

    job_number = models.CharField(max_length=20, unique=True, editable=False)
    customer_name = models.CharField(max_length=200)
    customer_address = models.TextField(blank=True)
    po_number = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField(help_text="Total number of certificates for this job")
    committed_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="jobs_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.job_number} — {self.customer_name}"

    @staticmethod
    def generate_job_number():
        """Format: YYMM + 3-digit sequence reset every month, e.g. 2609001."""
        now = timezone.localdate()
        prefix = now.strftime("%y%m")
        last = (
            Job.objects.filter(job_number__startswith=prefix)
            .aggregate(Max("job_number"))
            .get("job_number__max")
        )
        seq = int(last[-3:]) + 1 if last else 1
        return f"{prefix}{seq:03d}"

    def save(self, *args, **kwargs):
        if not self.job_number:
            self.job_number = self.generate_job_number()
        super().save(*args, **kwargs)

    @property
    def certificates_assigned_count(self):
        return sum(li.quantity for li in self.line_items.all())

    @property
    def certificates_remaining(self):
        return self.quantity - self.certificates_assigned_count


class Certificate(models.Model):
    """One certificate slot generated for a Job."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="certificates")
    number = models.CharField(max_length=20, unique=True, editable=False)
    line_item = models.ForeignKey(
        "JobLineItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates"
    )

    # --- Device under test ---
    device_serial = models.CharField(max_length=100, blank=True)
    device_manufacturer = models.CharField(max_length=150, blank=True)
    device_tag_number = models.CharField(max_length=100, blank=True)

    device_resolution = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
    )

    device_accuracy = models.CharField(max_length=100, blank=True)

    device_ratio = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
    )

    reference_instrument_range = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
    )

    uuc_full_scale = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
    )

    uuc_unit = models.CharField(
        max_length=20,
        blank=True,
    )

    pressure_media = models.CharField(
        max_length=30,
        blank=True,
    )

    # --- Working standard ---
    working_standard_name = models.CharField(max_length=150, blank=True)
    working_standard_serial = models.CharField(max_length=100, blank=True)
    working_standard_certificate_no = models.CharField(max_length=100, blank=True)

    # --- Calibration conditions ---
    lab_temperature = models.CharField(max_length=50, blank=True)
    lab_humidity = models.CharField(max_length=50, blank=True)
    ambient_pressure = models.CharField(max_length=50, blank=True)
    reference_procedure = models.CharField(max_length=150, blank=True)
    temperature_variation = models.CharField(max_length=50, blank=True)
    condition_notes = models.TextField(blank=True)

    # --- Sign-off ---
    calibration_date = models.DateField(null=True, blank=True)
    re_calibration_date = models.DateField(null=True, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    calibrated_by = models.CharField(max_length=150, blank=True)
    approved_signatory = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.number

    @staticmethod
    def generate_number():
        last = Certificate.objects.aggregate(Max("number")).get("number__max")
        seq = int(last.split("-")[1]) + 1 if last else 116444
        return f"AF-{seq}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self.generate_number()

        # ---------------------------------------------------------
        # Automatically derive UUC full scale from Job Line Item
        # ---------------------------------------------------------
        if self.line_item_id:
            if self.line_item.range_to is not None:
                self.uuc_full_scale = self.line_item.range_to

            if self.line_item.unit:
                self.uuc_unit = self.line_item.unit

            if self.line_item.job_id:
                pass

        super().save(*args, **kwargs)

    @property
    def is_complete(self):
        return bool(
            self.calibration_date
            and self.calibrated_by
            and self.results.exists()
        )

    # -------------------------------------------------------------
    # Pressure conversion helpers
    # -------------------------------------------------------------

    @property
    def uuc_full_scale_bar(self):
        """
        Convert UUC full scale from its selected unit to bar.

        CMC ranges are defined in bar, so CMC selection always
        uses this value.
        """

        if self.uuc_full_scale is None:
            return None

        unit = (self.uuc_unit or "bar").strip().lower()

        value = Decimal(str(self.uuc_full_scale))

        conversions_to_bar = {
            "bar": Decimal("1"),
            "psi": Decimal("1") / Decimal("14.5038"),
            "kpa": Decimal("1") / Decimal("100"),
            "mpa": Decimal("10"),
            "kg/cm2": Decimal("1") / Decimal("1.01972"),
            "kgf/cm²": Decimal("1") / Decimal("1.01972"),
        }

        factor = conversions_to_bar.get(unit)

        if factor is None:
            return None

        return value * factor

    @property
    def assumed_resolution(self):
        """
        Assumed Resolution = Resolution × Ratio
        """

        if self.device_resolution is None:
            return None

        if self.device_ratio is None:
            return None

        return self.device_resolution * self.device_ratio


class CalibrationResult(models.Model):
    """One row of a certificate's calibration results table."""

    certificate = models.ForeignKey(
        Certificate,
        on_delete=models.CASCADE,
        related_name="results",
    )

    # Applied value / pressure
    applied_value = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Applied Pressure (A)",
    )

    # Increasing pressure reading
    upward_reading = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Upward (M1)",
    )

    # Decreasing pressure reading
    downward_reading = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Downward (M2)",
    )

    # Mean of M1 and M2
    mean_value = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Mean Value (M)",
    )

    # Applied - Mean
    deviation = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Deviation (A-M)",
    )

    # --- Uncertainty / Instrument Setup ---

    reference_instrument_range = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
    )

    uuc_full_scale = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
    )

    uuc_unit = models.CharField(
        max_length=20,
        blank=True,
    )

    pressure_media = models.CharField(
        max_length=30,
        blank=True,
    )

    # Expanded uncertainty
    uncertainty = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Expanded Uncertainty (% FS)",
    )

    # Pass / Fail
    remarks = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Remarks",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.certificate.number} - {self.applied_value} bar"

REFERENCE_PROCEDURE_CHOICES = [
    ("LCP/AAF/001", "LCP/AAF/001"),
    ("LCP/AAF/002", "LCP/AAF/002"),
    ("LCP/AAF/003", "LCP/AAF/003"),
    ("LCP/AAF/004", "LCP/AAF/004"),
    ("LCP/AAF/005", "LCP/AAF/005"),
]

UNIT_CHOICES = [
    ("bar", "bar"),
    ("psi", "psi"),
    ("kPa", "kPa"),
    ("MPa", "MPa"),
    ("kg/cm2", "kg/cm2"),
]


class JobLineItem(models.Model):
    """One row of the job's instrument table. `quantity` certificates from
    the job's pool get attached to this row, in order, on save."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="line_items")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="line_items")
    model = models.CharField(max_length=150, blank=True)
    range_from = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    range_to = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=50, blank=True, null=True, choices=UNIT_CHOICES)
    quantity = models.PositiveIntegerField(default=1)
    assigned_to = models.ForeignKey(User, on_delete=models.PROTECT,  related_name="assigned_line_items", limit_choices_to={"role": User.Role.TECHNICIAN},)
    calibration_points = models.PositiveIntegerField(default=1)
    calibration_validity = models.CharField(max_length=100, blank=True) 
    reference_procedure = models.CharField(max_length=150, blank=True, choices=REFERENCE_PROCEDURE_CHOICES)

    def __str__(self):
        return f"{self.instrument} x{self.quantity} ({self.job.job_number})"


    @property
    def certificate_range(self):
        numbers = list(
            self.certificates.order_by("number").values_list("number", flat=True)
        )
        if not numbers:
            return "—"
        if len(numbers) == 1:
            return numbers[0]
        return f"{numbers[0]} – {numbers[-1]}"

    def assign_certificates(self):
        """
        Assign the next available certificates to this line item.

        The certificate automatically inherits:
        - reference procedure
        - UUC full scale
        - UUC unit
        """

        available = (
            self.job.certificates
            .filter(line_item__isnull=True)
            .order_by("number")[:self.quantity]
        )

        for cert in available:
            cert.line_item = self
            cert.reference_procedure = self.reference_procedure

            # ---------------------------------------------------------
            # Automatically derive UUC setup from Job Line Item
            # ---------------------------------------------------------

            cert.uuc_full_scale = self.range_to
            cert.uuc_unit = self.unit or ""

            cert.save(update_fields=[
                "line_item",
                "reference_procedure",
                "uuc_full_scale",
                "uuc_unit",
            ])


def job_document_path(instance, filename):
    return f"job_documents/{instance.job.job_number}/{filename}"


class JobDocument(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="documents")
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to=job_document_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
