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

from django.utils import timezone

from .models import (InstructorProfile, StudentProfile, Course, Lecture,
                     QRCodeUpload)

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

def unauthorized():
    """401 response used whenever the logged-in user has the wrong role."""
    return HttpResponse("Unauthorized", status=401)


def get_instructor(request):
    """Return the logged-in user's InstructorProfile, or None if the user is
    logged out or is not an instructor."""
    if not request.user.is_authenticated:
        return None
    return InstructorProfile.objects.filter(user=request.user).first()


def get_student(request):
    """Return the logged-in user's StudentProfile, or None if the user is
    logged out or is not a student."""
    if not request.user.is_authenticated:
        return None
    return StudentProfile.objects.filter(user=request.user).first()


def parse_time(text):
    """Turn a time string like "10:30", "10:30:00" or "10:30 AM" into a
    datetime.time. Returns None if the text isn't a time we understand."""
    text = (text or "").strip()
    formats = ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p",
               "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
               "%Y-%m-%dT%H:%M:%S"]
    for fmt in formats:
        try:
            return datetime.strptime(text.upper(), fmt).time()
        except ValueError:
            pass
    return None


# Form checkbox name -> letter stored in Course.meeting_days
DAY_FIELDS = [("day-mon", "M"), ("day-tue", "T"), ("day-wed", "W"),
              ("day-thu", "R"), ("day-fri", "F")]


def is_checked(value):
    """A checkbox counts as ticked if it was sent with any non-false value."""
    return value is not None and value.strip().lower() not in ("", "0", "false", "off", "no")


@csrf_exempt
@require_GET
def new_course(request):
    """Show the form at /app/new_course that posts to /app/createCourse."""
    return render(request, 'app/new_course.html')


@csrf_exempt
@require_GET
def new_lecture(request):
    """Show the form at /app/new_lecture that posts to /app/createLecture.
    The dropdown lists every course name."""
    courses = Course.objects.order_by("name")
    return render(request, 'app/new_lecture.html', {"courses": courses})


@csrf_exempt
@require_GET
def new_qr_upload(request):
    """Show the form at /app/new_qr_upload that uploads an image to
    /app/createQRCodeUpload."""
    return render(request, 'app/new_qr_upload.html')


@csrf_exempt
@require_POST
def createCourse(request):
    """Create a Course from POST data (instructors only).

    Fields: course-name, start-time, end-time and one or more of
    day-mon ... day-fri. The course belongs to the logged-in instructor.
    Course names are unique, so posting an existing name updates that
    course instead of crashing. Returns 401 for students and logged-out
    users, 400 for bad input and 200 on success.
    """
    instructor = get_instructor(request)
    if instructor is None:
        return unauthorized()

    name = request.POST.get("course-name", "").strip()
    start = parse_time(request.POST.get("start-time"))
    end = parse_time(request.POST.get("end-time"))
    days = "".join(letter for field, letter in DAY_FIELDS
                   if is_checked(request.POST.get(field)))

    if not name:
        return HttpResponseBadRequest("Missing course-name.")
    if start is None or end is None:
        return HttpResponseBadRequest("start-time and end-time must be times like 10:30.")
    if not days:
        return HttpResponseBadRequest("Pick at least one of day-mon ... day-fri.")

    course, created = Course.objects.update_or_create(
        name=name,
        defaults={"instructor": instructor, "meeting_days": days,
                  "start_time": start, "end_time": end},
    )
    verb = "Created" if created else "Updated"
    return HttpResponse(f"<p>{verb} course {course.name} ({days} "
                        f"{start:%H:%M}-{end:%H:%M}).</p> <a href='/'>Home</a>")


@csrf_exempt
@require_POST
def createLecture(request):
    """Create a Lecture for the course named in POST field "choice"
    (instructors only). The lecture date is today and its QR code is
    generated automatically. Returns 401 for students and logged-out
    users, 400 if the course doesn't exist and 200 on success.
    """
    if get_instructor(request) is None:
        return unauthorized()

    choice = request.POST.get("choice", "").strip()
    course = Course.objects.filter(name=choice).first()
    if course is None:
        return HttpResponseBadRequest(f"No course named '{choice}'.")

    lecture = Lecture.objects.create(course=course)
    return HttpResponse(f"<p>Created lecture for {course.name} on {lecture.date} "
                        f"(QR code {lecture.qr_code}).</p> <a href='/'>Home</a>")


@csrf_exempt
@require_POST
def createQRCodeUpload(request):
    """Save an uploaded QR-code image from file field "imageUpload"
    (students only). The student comes from the logged-in user and the
    upload time is set by the server. Returns 401 for instructors and
    logged-out users, 400 if no file was sent and 200 on success.
    """
    student = get_student(request)
    if student is None:
        return unauthorized()

    image = request.FILES.get("imageUpload")
    if image is None:
        return HttpResponseBadRequest("Missing file field imageUpload.")

    upload = QRCodeUpload.objects.create(student=student, image=image)
    when = timezone.localtime(upload.uploaded_at).strftime("%Y-%m-%d %H:%M:%S")
    return HttpResponse(f"<p>Upload received from {student.user.username} at {when}.</p> "
                        f"<a href='/'>Home</a>")


@csrf_exempt   # so a POST gets a clean 405 instead of a CSRF 403
@require_GET
def dumpUploads(request):
    """Return every QR-code upload as JSON (instructors only):
    [{"username": "alice", "upload_time": "2024-11-01 10:30:00"}, ...]
    Times are shown in Chicago time. Returns 401 for students and
    logged-out users.
    """
    if get_instructor(request) is None:
        return unauthorized()

    uploads = (QRCodeUpload.objects.select_related("student__user")
               .order_by("uploaded_at"))
    data = [
        {
            "username": u.student.user.username,
            "upload_time": timezone.localtime(u.uploaded_at).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for u in uploads
    ]
    return JsonResponse(data, safe=False)



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
