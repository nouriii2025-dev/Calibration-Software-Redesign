from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CalibrationResult, Certificate, Instrument, Job, JobDocument, JobLineItem, User


@admin.register(User)
class LabUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Role", {"fields": ("role",)}),)
    list_display = ("username", "first_name", "last_name", "role", "is_active")


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ("name", "default_model", "default_range", "is_active")


class JobLineItemInline(admin.TabularInline):
    model = JobLineItem
    extra = 0


class JobDocumentInline(admin.TabularInline):
    model = JobDocument
    extra = 0


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("job_number", "customer_name", "po_number", "quantity", "created_by", "created_at")
    inlines = [JobLineItemInline, JobDocumentInline]
    readonly_fields = ("job_number",)


class CalibrationResultInline(admin.TabularInline):
    model = CalibrationResult
    extra = 0


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ("number", "job", "line_item", "is_complete")
    list_filter = ("job",)
    inlines = [CalibrationResultInline]
