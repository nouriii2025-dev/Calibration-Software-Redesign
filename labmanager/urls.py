from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.RoleAwareLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="login"), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("jobs/new/", views.job_create, name="job_create"),
    path("jobs/<int:pk>/", views.job_detail, name="job_detail"),
    path("certificates/<int:pk>/", views.certificate_detail, name="certificate_detail"),
    path("instruments/", views.instrument_list, name="instrument_list"),
    path("instruments/<int:pk>/toggle/", views.instrument_toggle, name="instrument_toggle"),
    path("technicians/", views.technician_list, name="technician_list"),
    path("technicians/<int:pk>/toggle/", views.technician_toggle, name="technician_toggle"),
    path("certificates/<int:pk>/print/",views.certificate_print,name="certificate_print",),
    path(
    "uncertainty/<int:pk>/",
    views.uncertainty,
    name="uncertainty",
    ),
    path(
    "certificate/<int:pk>/save-uncertainty/",
    views.save_uncertainty,
    name="save_uncertainty",
    ),
]
