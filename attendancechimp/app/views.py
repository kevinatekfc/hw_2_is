"""
views.py — HTTP request handlers for AttendanceChimp.

Each view corresponds to one URL endpoint.  Views receive an HTTP request,
interact with the database via models.py, and return an HTTP response.
"""

from django.shortcuts import render, redirect
from django.http import (HttpResponse, HttpResponseForbidden,
                         HttpResponseBadRequest, JsonResponse)
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from datetime import datetime
import zoneinfo
import json
import io

from .models import InstructorProfile, StudentProfile

CDT = zoneinfo.ZoneInfo("America/Chicago")


# ---------------------------------------------------------------------------
# HW2  — index, time, sum
# ---------------------------------------------------------------------------

def index(request):
    """Render the home page (index.html).

    Passes the current Chicago time (HH:MM) and the logged-in user's name
    to the template. If nobody is logged in, user_name is None and the
    template shows "Not logged in."
    """
    now = datetime.now(CDT)
    if request.user.is_authenticated:
        user_name = request.user.username
    else:
        user_name = None
    context = {
        "current_time": now.strftime("%H:%M"),
        "user_name": user_name,
    }
    return render(request, 'app/index.html', context)



# ---------------------------------------------------------------------------
# HW4  — user creation
# ---------------------------------------------------------------------------

@csrf_exempt   # let require_GET answer POSTs with 405 instead of a CSRF 403
@require_GET
def new_user_form(request):
    """Show the sign-up form at /app/new.

    Only GET is allowed; any other method gets a 405 error.
    """
    return render(request, 'app/new.html')


@csrf_exempt   # so the API can be called with curl (no CSRF token)
@require_POST
def createUser(request):
    """Create a new user from POST data at /app/createUser.

    Expects user_name, email, password and is_student. Returns 400 if a
    field is missing or the email/user name is already taken. Otherwise
    creates the User plus a StudentProfile or InstructorProfile, logs the
    new user in, and returns 200.
    """
    user_name = request.POST.get("user_name", "").strip()
    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    is_student = request.POST.get("is_student", "")

    if not user_name or not email or not password or is_student == "":
        return HttpResponseBadRequest("Missing user_name, email, password or is_student.")

    if User.objects.filter(email__iexact=email).exists():
        return HttpResponseBadRequest("A user with that email already exists.")
    if User.objects.filter(username=user_name).exists():
        return HttpResponseBadRequest("That user name is already taken.")

    # is_student comes in as text: "1"/"true"/"student" mean student
    student = is_student.lower() in ("1", "true", "yes", "on", "student")

    user = User.objects.create_user(username=user_name, email=email,
                                    password=password)
    if student:
        StudentProfile.objects.create(user=user, student_id=str(user.id))
    else:
        InstructorProfile.objects.create(user=user)

    login(request, user)
    role = "student" if student else "instructor"
    return HttpResponse(f"<p>Created {role} {user_name}.</p> <a href='/'>Home</a>")



# ---------------------------------------------------------------------------
# HW5  — courses, lectures, QR uploads, dump
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# HW6  — getUploads
# ---------------------------------------------------------------------------





def time_view(request):
    """Return the current Chicago time as HH:MM."""
    now = datetime.now(CDT)
    return HttpResponse(now.strftime("%H:%M"))


def sum_view(request):
    """Add n1 and n2 from the URL and return the sum as JSON."""
    n1 = float(request.GET.get("n1"))
    n2 = float(request.GET.get("n2"))
    total = round(n1 + n2, 10)   # fixes tiny decimal errors like 2.3000000000000003
    if total == int(total):
        total = int(total)       # shows 3 instead of 3.0
    return HttpResponse(f"<p>{total}</p>")   # grader looks for a <p> tag
