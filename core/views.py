"""
LabAI Core Views
All API endpoints + page views using Django's class-based and function-based views.
"""

import json
import hashlib
import logging
import threading

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import transaction

from .models import TrainingData, UserUpload, ModelWeights, BatchJob, BatchItem
from .engine import (
    OCR_AVAILABLE, NUMPY_AVAILABLE,
    FEATURE_KEYS, REF,
    ocr_image_bytes, parse_lab_values,
    analyze_value, extract_feature_vector, compute_stats,
)

log = logging.getLogger("labai.views")


# ═══════════════════════════════════════════════════════════════════
#  AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════

def _make_token(password: str) -> str:
    key = f"{password}:{settings.SECRET_KEY}"
    return hashlib.sha256(key.encode()).hexdigest()


def _check_token(request) -> bool:
    token = request.headers.get("X-Admin-Token", "")
    return token == _make_token(settings.ADMIN_PASSWORD)


def _require_admin(view_func):
    """Decorator — returns 401 if admin token missing/wrong."""
    def wrapper(request, *args, **kwargs):
        if not _check_token(request):
            return JsonResponse({"error": "Unauthorized"}, status=401)
        return view_func(request, *args, **kwargs)
    return wrapper


# ═══════════════════════════════════════════════════════════════════
#  PAGE VIEWS
# ═══════════════════════════════════════════════════════════════════

def index_view(request):
    return render(request, "index.html")

def user_view(request):
    return render(request, "user.html")

def admin_view(request):
    return render(request, "admin.html")

def health_view(request):
    return JsonResponse({
        "status": "ok",
        "ocr": OCR_AVAILABLE,
        "numpy": NUMPY_AVAILABLE,
        "db": str(settings.DATABASES["default"]["NAME"]),
    })


# ═══════════════════════════════════════════════════════════════════
#  AUTH API
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
def api_login(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    pw = data.get("password", "")
    if hashlib.sha256(pw.encode()).hexdigest() == hashlib.sha256(
        settings.ADMIN_PASSWORD.encode()
    ).hexdigest():
        return JsonResponse({"ok": True, "token": _make_token(pw)})
    return JsonResponse({"ok": False, "error": "Invalid password"}, status=401)


# ═══════════════════════════════════════════════════════════════════
#  USER: OCR ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
def api_ocr(request):
    """
    POST /api/ocr  — multipart/form-data with 'file'
    Server preprocesses image → OCR → parse → analyze → return JSON
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    if not OCR_AVAILABLE:
        return JsonResponse({"error": "OCR not available on server"}, status=503)

    uploaded = request.FILES.get("file")
    if not uploaded:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    image_bytes = uploaded.read()
    text = ocr_image_bytes(image_bytes)
    raw_values = parse_lab_values(text)

    if not raw_values:
        return JsonResponse({
            "ok": False,
            "error": "No lab values found in this image",
            "text_preview": text[:400],
        })

    analyzed = {}
    flagged = 0
    for name, v in raw_values.items():
        status = analyze_value(name, v)
        analyzed[name] = {"value": v, "status": status}
        if status not in ("normal", "unknown"):
            flagged += 1

    return JsonResponse({
        "ok": True,
        "values": analyzed,
        "flagged": flagged,
        "total": len(analyzed),
    })


# ═══════════════════════════════════════════════════════════════════
#  USER: SAVE UPLOAD
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
def api_user_save(request):
    """POST /api/user/save — save anonymous user values to DB."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False})

    values = data.get("values", {})
    if not values:
        return JsonResponse({"ok": False})

    flagged = sum(
        1 for name, v in values.items()
        if analyze_value(name, float(v)) not in ("normal", "unknown")
    )
    clean = {k: float(v) for k, v in values.items()}

    UserUpload.objects.create(
        filename=data.get("filename", "unknown")[:200],
        val_count=len(clean),
        flagged_cnt=flagged,
        ml_score=data.get("ml_score"),
        values_json=json.dumps(clean),
    )
    return JsonResponse({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  MODEL: SERVE CURRENT WEIGHTS
# ═══════════════════════════════════════════════════════════════════

def api_model_current(request):
    """GET /api/model/current — return deployed TF.js weights to all users."""
    try:
        m = ModelWeights.objects.get(model_id="current")
        return JsonResponse({
            "ok": True,
            "weights": json.loads(m.weights_json),
            "stats":   json.loads(m.stats_json),
            "version": m.version,
            "training_size": m.training_size,
        })
    except ModelWeights.DoesNotExist:
        return JsonResponse({"ok": False, "error": "No model deployed yet"})


# ═══════════════════════════════════════════════════════════════════
#  ADMIN: BATCH UPLOAD
# ═══════════════════════════════════════════════════════════════════

def _process_batch_job(job_id: int, files_data: list):
    """
    Background thread: OCR each image → update BatchItem records.
    Uses direct sqlite3 connection (WAL mode) to avoid Django ORM
    thread-safety issues with SQLite.
    """
    import sqlite3 as _sqlite3
    db_path = str(settings.DATABASES["default"]["NAME"])
    conn = _sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.execute("UPDATE batch_jobs SET status='running' WHERE id=?", (job_id,))
        conn.commit()

        for filename, image_bytes in files_data:
            conn.execute(
                "UPDATE batch_items SET status='processing' WHERE job_id=? AND filename=?",
                (job_id, filename),
            )
            conn.commit()

            try:
                text   = ocr_image_bytes(image_bytes)
                values = parse_lab_values(text)

                if len(values) >= 2:
                    conn.execute(
                        "UPDATE batch_items SET status='ready', val_count=?, values_json=? "
                        "WHERE job_id=? AND filename=?",
                        (len(values), json.dumps(values), job_id, filename),
                    )
                    conn.execute(
                        "UPDATE batch_jobs SET processed=processed+1 WHERE id=?",
                        (job_id,),
                    )
                else:
                    conn.execute(
                        "UPDATE batch_items SET status='skipped' "
                        "WHERE job_id=? AND filename=?",
                        (job_id, filename),
                    )
                    conn.execute(
                        "UPDATE batch_jobs SET processed=processed+1, skipped=skipped+1 "
                        "WHERE id=?",
                        (job_id,),
                    )
                conn.commit()

            except Exception as e:
                log.error(f"Batch item error [{filename}]: {e}")
                conn.execute(
                    "UPDATE batch_items SET status='failed', error=? "
                    "WHERE job_id=? AND filename=?",
                    (str(e)[:200], job_id, filename),
                )
                conn.execute(
                    "UPDATE batch_jobs SET processed=processed+1, failed=failed+1 "
                    "WHERE id=?",
                    (job_id,),
                )
                conn.commit()

        conn.execute("UPDATE batch_jobs SET status='done' WHERE id=?", (job_id,))
        conn.commit()

    except Exception as e:
        log.error(f"Batch job {job_id} fatal error: {e}")
        conn.execute("UPDATE batch_jobs SET status='error' WHERE id=?", (job_id,))
        conn.commit()
    finally:
        conn.close()


@csrf_exempt
@_require_admin
def api_batch_start(request):
    """
    POST /api/admin/batch/start  — multipart, field name 'files'
    Creates BatchJob + BatchItems, spawns background thread.
    Returns job_id immediately.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    files = request.FILES.getlist("files")
    if not files:
        return JsonResponse({"error": "No files uploaded"}, status=400)

    # Read all files into memory before spawning thread
    files_data = [(f.name[:200], f.read()) for f in files]

    with transaction.atomic():
        job = BatchJob.objects.create(total=len(files_data))
        BatchItem.objects.bulk_create([
            BatchItem(job=job, filename=fname, status="waiting")
            for fname, _ in files_data
        ])

    # Spawn background OCR thread
    t = threading.Thread(
        target=_process_batch_job,
        args=(job.id, files_data),
        daemon=True,
    )
    t.start()

    return JsonResponse({"ok": True, "job_id": job.id, "total": len(files_data)})


def api_batch_status(request, job_id):
    """GET /api/admin/batch/<job_id>/status — poll job progress."""
    try:
        job = BatchJob.objects.get(id=job_id)
    except BatchJob.DoesNotExist:
        return JsonResponse({"error": "Job not found"}, status=404)

    items = job.items.all().values(
        "filename", "status", "val_count", "values_json", "error"
    )
    return JsonResponse({
        "job_id": job_id,
        "total":     job.total,
        "processed": job.processed,
        "saved":     job.saved,
        "skipped":   job.skipped,
        "failed":    job.failed,
        "status":    job.status,
        "items": [
            {
                "filename":  i["filename"],
                "status":    i["status"],
                "val_count": i["val_count"],
                "values":    json.loads(i["values_json"]) if i["values_json"] else {},
                "error":     i["error"],
            }
            for i in items
        ],
    })


@csrf_exempt
@_require_admin
def api_batch_approve(request, job_id, filename):
    """POST — admin approves (with optional edits) → save to TrainingData."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    values = data.get("values", {})
    if not values:
        return JsonResponse({"error": "No values provided"}, status=400)

    clean = {k: float(v) for k, v in values.items() if v is not None}
    features = extract_feature_vector(clean)

    with transaction.atomic():
        TrainingData.objects.create(
            source="admin",
            filename=filename[:200],
            val_count=len(clean),
            values_json=json.dumps(clean),
            features=json.dumps(features),
        )
        BatchItem.objects.filter(job_id=job_id, filename=filename).update(status="saved")
        BatchJob.objects.filter(id=job_id).update(saved=BatchJob.objects.get(id=job_id).saved + 1)

    return JsonResponse({"ok": True})


@csrf_exempt
@_require_admin
def api_batch_skip(request, job_id, filename):
    """POST — admin skips a batch item."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    BatchItem.objects.filter(job_id=job_id, filename=filename).update(status="skipped")
    job = BatchJob.objects.get(id=job_id)
    BatchJob.objects.filter(id=job_id).update(skipped=job.skipped + 1)
    return JsonResponse({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  ADMIN: DASHBOARD + DATA
# ═══════════════════════════════════════════════════════════════════

@_require_admin
def api_admin_stats(request):
    train_count  = TrainingData.objects.count()
    upload_count = UserUpload.objects.count()
    flagged      = UserUpload.objects.filter(flagged_cnt__gt=0).count()

    model_data = None
    try:
        m = ModelWeights.objects.get(model_id="current")
        model_data = {
            "version": m.version,
            "training_size": m.training_size,
            "trained_at": m.trained_at.isoformat() if m.trained_at else None,
        }
    except ModelWeights.DoesNotExist:
        pass

    recent = list(
        UserUpload.objects.values(
            "filename", "val_count", "flagged_cnt", "ml_score", "created_at"
        ).order_by("-id")[:20]
    )
    for r in recent:
        if r["created_at"]:
            r["created_at"] = r["created_at"].isoformat()

    return JsonResponse({
        "train_count":    train_count,
        "upload_count":   upload_count,
        "flagged":        flagged,
        "model":          model_data,
        "recent_uploads": recent,
    })


@_require_admin
def api_admin_training(request):
    records = list(
        TrainingData.objects.values(
            "id", "source", "filename", "val_count", "created_at"
        ).order_by("-id")[:500]
    )
    for r in records:
        if r["created_at"]:
            r["created_at"] = r["created_at"].isoformat()
    return JsonResponse({"records": records})


@csrf_exempt
@_require_admin
def api_delete_training(request, record_id):
    if request.method != "DELETE":
        return JsonResponse({"error": "DELETE only"}, status=405)
    TrainingData.objects.filter(id=record_id).delete()
    return JsonResponse({"ok": True})


@_require_admin
def api_admin_uploads(request):
    uploads = list(
        UserUpload.objects.values(
            "id", "filename", "val_count", "flagged_cnt", "ml_score",
            "values_json", "created_at"
        ).order_by("-id")[:500]
    )
    for u in uploads:
        if u["created_at"]:
            u["created_at"] = u["created_at"].isoformat()
    return JsonResponse({"uploads": uploads})


@csrf_exempt
@_require_admin
def api_clear_uploads(request):
    if request.method != "DELETE":
        return JsonResponse({"error": "DELETE only"}, status=405)
    UserUpload.objects.all().delete()
    return JsonResponse({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  ADMIN: MODEL TRAINING
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
@_require_admin
def api_train_model(request):
    """
    POST /api/admin/model/train
    Server gathers training vectors, computes stats (numpy), returns
    feature matrix to browser for TF.js training.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    if not NUMPY_AVAILABLE:
        return JsonResponse({"error": "numpy not installed on server"}, status=503)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    source    = data.get("source", "both")
    min_tests = int(data.get("min_tests", 5))

    qs_train  = TrainingData.objects.none()
    qs_user   = UserUpload.objects.none()

    if source in ("admin", "both"):
        qs_train = TrainingData.objects.filter(source="admin")
    if source in ("user", "both"):
        qs_user  = UserUpload.objects.filter(val_count__gte=min_tests)

    matrix = []
    for obj in list(qs_train) + list(qs_user):
        try:
            vj  = json.loads(obj.values_json)
            vec = [float(vj.get(k, 0) or 0) for k in FEATURE_KEYS]
            if sum(1 for v in vec if v > 0) >= 2:
                matrix.append(vec)
        except Exception:
            continue

    if len(matrix) < 5:
        return JsonResponse(
            {"error": f"Only {len(matrix)} usable records. Need at least 5."},
            status=400,
        )

    stats = compute_stats(matrix)

    return JsonResponse({
        "ok":           True,
        "record_count": len(matrix),
        "feature_keys": FEATURE_KEYS,
        "stats":        stats,
        "matrix":       matrix,
    })


@csrf_exempt
@_require_admin
def api_deploy_model(request):
    """POST /api/admin/model/deploy — store trained TF.js weights in DB."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    weights = data.get("weights")
    stats   = data.get("stats")
    size    = int(data.get("training_size", 0))

    if not weights or not stats:
        return JsonResponse({"error": "Missing weights or stats"}, status=400)

    obj, created = ModelWeights.objects.get_or_create(model_id="current")
    if not created:
        obj.version += 1
    obj.weights_json  = json.dumps(weights)
    obj.stats_json    = json.dumps(stats)
    obj.training_size = size
    obj.trained_at    = timezone.now()
    obj.save()

    return JsonResponse({"ok": True, "version": obj.version})