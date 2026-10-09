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

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.LAB_HEAD)

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


LOCATION_CHOICES = [
    ("Lab", "Lab"),
    ("On-site", "On-site"),     
]

class Job(models.Model):
    """A single calibration job created by a Lab Head. job_number and the
    pool of certificates are generated automatically from `quantity`."""

    job_number = models.CharField(max_length=20, unique=True, editable=False)
    customer_name = models.CharField(max_length=200)
    customer_address = models.TextField(blank=True)
    telephone_number = models.CharField(
        max_length=50,
        blank=True
    )

    fax_number = models.CharField(
        max_length=50,
        blank=True
    )

    location = models.CharField(
        max_length=200,
        blank=True,
        choices=LOCATION_CHOICES
    )

    scope_of_work = models.CharField(
        max_length=500,
        blank=True
    )
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

ACCURACY_UNIT_CHOICES = [
    ("FS", "FS"),
    ("Rdg", "Rdg"),
]


class Certificate(models.Model):
    """One certificate slot generated for a Job."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="certificates")
    number = models.CharField(max_length=20, unique=True, editable=False)
    datasheet_number = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        blank=True,
        null=True
    )
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
    device_accuracy_unit = models.CharField(
        max_length=10,
        choices=ACCURACY_UNIT_CHOICES,
        default="FS",
        blank=True,
    )

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
    instrument_received_good = models.BooleanField(
        default=True,
        verbose_name="Instrument is received in good condition"
    )
    instrument_adjusted = models.BooleanField(
        default=False,
        verbose_name="Instrument is adjusted and post adjustment values are reported"
    )
    no_adjustment = models.BooleanField(
        default=True,
        verbose_name="No adjustment is carried out and measurements in this certificate are as received figures"
    )

    # --- Sign-off ---
    calibration_date = models.DateField(null=True, blank=True)
    re_calibration_date = models.DateField(null=True, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    calibrated_by = models.CharField(max_length=150, blank=True)
    approved_signatory = models.CharField(max_length=150, blank=True)

    class ApprovalStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Approval"
        APPROVED = "approved", "Approved"

    approval_status = models.CharField(max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_certificates")

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.number

    @property
    def is_draft(self): return self.approval_status == self.ApprovalStatus.DRAFT
    @property
    def is_pending_approval(self): return self.approval_status == self.ApprovalStatus.PENDING
    @property
    def is_approved(self): return self.approval_status == self.ApprovalStatus.APPROVED
    @property
    def assigned_technician(self): return self.line_item.assigned_to if self.line_item else None

    def submit_for_approval(self, user):
        if not self.is_draft:
            return False, "This certificate has already been sent for approval."
        if not self.is_complete:
            return False, "Complete the calibration date, 'Calibrated By' and at least one result row before sending for approval."
        self.approval_status = self.ApprovalStatus.PENDING
        self.submitted_at = timezone.now()
        self.save(update_fields=["approval_status", "submitted_at"])
        for head in User.objects.filter(role=User.Role.LAB_HEAD, is_active=True):
            Notification.send(head, f"Certificate {self.number} (Job {self.job.job_number}) was sent for approval by {user}.", certificate=self)
        return True, f"Certificate {self.number} sent to the Lab Head for approval."

    def approve(self, user):
        if self.is_approved:
            return False, "This certificate is already approved."
        if not self.is_complete:
            return False, "This certificate is incomplete and cannot be approved yet."
        self.approval_status = self.ApprovalStatus.APPROVED
        self.approved_at = timezone.now()
        self.approved_by = user
        self.save(update_fields=["approval_status", "approved_at", "approved_by"])
        tech = self.assigned_technician
        if tech and tech.pk != user.pk:
            Notification.send(tech, f"Certificate {self.number} (Job {self.job.job_number}) was approved by {user}. You can now print and export it.", certificate=self)
        return True, f"Certificate {self.number} approved."

    def return_to_technician(self, user):   # optional extra, see note at the end
        if not self.is_pending_approval:
            return False, "Only certificates pending approval can be returned."
        self.approval_status = self.ApprovalStatus.DRAFT
        self.submitted_at = None
        self.save(update_fields=["approval_status", "submitted_at"])
        tech = self.assigned_technician
        if tech and tech.pk != user.pk:
            Notification.send(tech, f"Certificate {self.number} (Job {self.job.job_number}) was returned by {user} for corrections.", certificate=self)
        return True, f"Certificate {self.number} returned to the technician."

    @staticmethod
    def generate_number():
        last = Certificate.objects.aggregate(Max("number")).get("number__max")
        seq = int(last.split("-")[1]) + 1 if last else 116444
        return f"AF-{seq}"

    @staticmethod
    def generate_datasheet_number():
        """
        Generate a unique sequential datasheet number.

        Examples:
        DS-0001
        DS-0002
        DS-0003
        """

        highest = 0

        for value in (
            Certificate.objects
            .filter(datasheet_number__startswith="DS-")
            .values_list("datasheet_number", flat=True)
        ):
            try:
                highest = max(highest, int(value.split("-")[-1]))
            except (ValueError, IndexError):
                continue

        seq = highest + 1

        return f"DS-{seq:04d}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self.generate_number()

        if not self.datasheet_number:
            self.datasheet_number = self.generate_datasheet_number()

        # ---------------------------------------------------------
        # Automatically derive UUC full scale from Job Line Item
        # ---------------------------------------------------------
        if self.line_item_id:
            if self.line_item.range_to is not None:
                self.uuc_full_scale = self.line_item.range_to

            if self.line_item.unit:
                self.uuc_unit = self.line_item.unit

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

CALIBRATION_VALIDITY_UNIT_CHOICES = [
    ("days", "Days"),
    ("months", "Months"),
    ("years", "Years"),
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
    # calibration_validity = models.CharField(max_length=100, blank=True) 
    calibration_validity_value = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Calibration Validity",
    )

    calibration_validity_unit = models.CharField(
        max_length=10,
        choices=CALIBRATION_VALIDITY_UNIT_CHOICES,
        default="years",
        blank=True,
    )
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

    def sync_certificates(self):
        """
        Synchronize certificates belonging to this line item
        with the current line-item configuration.
        """

        for cert in self.certificates.all():

            cert.reference_procedure = (
                self.reference_procedure or ""
            )

            cert.uuc_full_scale = self.range_to

            cert.uuc_unit = (
                self.unit or ""
            )

            cert.save(
                update_fields=[
                    "reference_procedure",
                    "uuc_full_scale",
                    "uuc_unit",
                ]
            )


def job_document_path(instance, filename):
    return f"job_documents/{instance.job.job_number}/{filename}"


class JobDocument(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="documents")
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to=job_document_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=255)
    certificate = models.ForeignKey(Certificate, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    job = models.ForeignKey(Job, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @classmethod
    def send(cls, recipient, message, certificate=None, job=None):
        return cls.objects.create(recipient=recipient, message=message[:255], certificate=certificate, job=job)

    @property
    def target_url(self):
        from django.urls import reverse
        if self.certificate_id:
            return reverse("certificate_detail", args=[self.certificate_id])
        if self.job_id:
            return reverse("job_detail", args=[self.job_id])
        return reverse("dashboard")