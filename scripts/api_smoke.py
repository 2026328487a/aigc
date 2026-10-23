"""Explicitly run one real DGX generation and verify local download and cleanup."""
import argparse
import json
import pathlib
import time

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["image", "reference"], default="image")
parser.add_argument("--width", type=int, default=768)
parser.add_argument("--height", type=int, default=448)
parser.add_argument("--seconds", type=float, default=2)
parser.add_argument("--steps", type=int, default=8)
parser.add_argument("--cancel", action="store_true")
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
source = root / "test-results/test-input.png"
settings = {"mode": args.mode, "width": args.width, "height": args.height,
            "seconds": args.seconds, "steps": args.steps, "seed": 42,
            "prompt": ("Use the red ball and plant from <Picture 1>. " if args.mode == "reference" else "") +
            "A red ball on a wooden table beside a small plant. The camera slowly pushes forward. Soft daylight from the window, a quiet room and gentle ambient sound."}
with httpx.Client(base_url="http://127.0.0.1:8765", headers={"X-AIGC-Client": "local"}, trust_env=False, timeout=90) as client:
    with source.open("rb") as stream:
        response = client.post("/api/jobs", data={"settings": json.dumps(settings)}, files={"images": ("sample.png", stream, "image/png")})
    response.raise_for_status()
    job_id = response.json()["id"]
    print("Submitted " + job_id, flush=True)
    if args.cancel:
        time.sleep(3)
        response = client.post(f"/api/jobs/{job_id}/cancel")
        response.raise_for_status()
    previous = ""
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        job = client.get("/api/jobs/" + job_id).json()
        current = f"{job['state']} | {job['stage']} | {job['elapsed']}s"
        if job["stage"] != previous:
            print(current, flush=True)
            previous = job["stage"]
        if job.get("cleanup_finished"):
            expected = "cancelled" if args.cancel else "completed"
            assert job["state"] == expected, json.dumps(job, ensure_ascii=False)
            assert job["remote_cleaned"], json.dumps(job, ensure_ascii=False)
            diagnostics = client.get("/api/diagnostics").json()
            assert diagnostics["remote"] is None or not diagnostics["remote"]["files"], diagnostics
            (root / f"test-results/{args.mode}{'-cancel' if args.cancel else ''}-result.json").write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({"id": job_id, "state": job["state"], "elapsed": job["elapsed"], "video": job.get("video"), "remote_cleaned": job["remote_cleaned"]}), flush=True)
            break
        time.sleep(2)
    else:
        raise TimeoutError("Smoke test exceeded 15 minutes; inspect the local UI")
