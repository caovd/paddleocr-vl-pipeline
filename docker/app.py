"""
PaddleOCR-VL Pipeline Adapter
Replaces broken PaddleX serve layer with a clean FastAPI service.
"""

import base64
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import JSONResponse
from pdf2image import convert_from_bytes
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("paddleocr-vl-adapter")

GOTENBERG_URL = os.getenv("GOTENBERG_URL", "http://localhost:3000")
VLM_SERVER_URL = os.getenv("VLM_SERVER_URL", "http://localhost:8080/v1")
VLM_MODEL_NAME = os.getenv("VLM_MODEL_NAME", "PaddlePaddle/PaddleOCR-VL")
MLIS_API_KEY = os.getenv("MLIS_API_KEY", "")
LAYOUT_DEVICE = os.getenv("LAYOUT_DEVICE", "cpu")
LAYOUT_ENABLED = os.getenv("LAYOUT_ENABLED", "true").lower() == "true"
LAYOUT_THRESHOLD = float(os.getenv("LAYOUT_THRESHOLD", "0.3"))
LAYOUT_MODEL_PATH = os.getenv("LAYOUT_MODEL_PATH", "/app/models/PP-DocLayoutV2.onnx")
LAYOUT_MODEL_REPO = os.getenv("LAYOUT_MODEL_REPO", "SWHL/PP-DocLayout-V2-ONNX")
LAYOUT_MODEL_FILE = os.getenv("LAYOUT_MODEL_FILE", "model.onnx")
HF_TOKEN = os.getenv("HF_TOKEN", "")

LAYOUT_LABELS = {
    0: "paragraph", 1: "picture", 2: "table", 3: "table_caption",
    4: "table_footnote", 5: "formula", 6: "formula_caption", 7: "abstract",
    8: "content", 9: "figure_caption", 10: "number", 11: "reference",
    12: "doc_title", 13: "code_block", 14: "header", 15: "footer",
    16: "algorithm", 17: "seal", 18: "chart", 19: "aside_text",
    20: "header_image", 21: "footer_image", 22: "footnote", 23: "title", 24: "toc",
}

IGNORE_LABELS = {"number", "footnote", "header", "header_image",
                 "footer", "footer_image", "aside_text"}

LABEL_TO_PROMPT = {
    "paragraph": "OCR:", "doc_title": "OCR:", "title": "OCR:",
    "abstract": "OCR:", "content": "OCR:", "reference": "OCR:",
    "code_block": "OCR:", "algorithm": "OCR:", "toc": "OCR:",
    "table": "Table Recognition:", "table_caption": "OCR:",
    "table_footnote": "OCR:", "formula": "Formula Recognition:",
    "formula_caption": "OCR:", "chart": "Chart Recognition:",
    "seal": "Seal Recognition:", "picture": "OCR:", "figure_caption": "OCR:",
}

CONVERT_EXTENSIONS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
                      ".odt", ".odp", ".ods", ".rtf"}

app = FastAPI(title="PaddleOCR-VL Pipeline Adapter", version="1.0.0")

layout_session = None
http_client: Optional[httpx.AsyncClient] = None


@app.on_event("startup")
async def startup():
    global layout_session, http_client
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0))

    if LAYOUT_ENABLED:
        import onnxruntime as ort
        model_path = LAYOUT_MODEL_PATH

        if not os.path.exists(model_path):
            logger.info(f"Downloading layout model from {LAYOUT_MODEL_REPO}...")
            try:
                from huggingface_hub import hf_hub_download
                os.makedirs(os.path.dirname(model_path), exist_ok=True)
                downloaded = hf_hub_download(
                    repo_id=LAYOUT_MODEL_REPO,
                    filename=LAYOUT_MODEL_FILE,
                    token=HF_TOKEN if HF_TOKEN else None,
                    local_dir="/app/models",
                    local_dir_use_symlinks=False,
                )
                if downloaded != model_path:
                    os.rename(downloaded, model_path)
                logger.info(f"Layout model downloaded to {model_path}")
            except Exception as e:
                logger.warning(f"Failed to download layout model: {e}")
                logger.warning("Layout detection disabled. Full pages will be sent to VLM.")
                layout_session = None
                return

        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                     if LAYOUT_DEVICE == "cuda"
                     else ["CPUExecutionProvider"])
        layout_session = ort.InferenceSession(model_path, providers=providers)
        logger.info(f"Layout loaded. Providers: {layout_session.get_providers()}")
    else:
        logger.info("Layout detection disabled via config.")

    logger.info(f"VLM endpoint: {VLM_SERVER_URL}")
    logger.info(f"Gotenberg endpoint: {GOTENBERG_URL}")


@app.on_event("shutdown")
async def shutdown():
    if http_client:
        await http_client.aclose()


@app.get("/health")
async def health():
    return {"status": "up", "layout_enabled": LAYOUT_ENABLED,
            "layout_loaded": layout_session is not None}


def preprocess_for_layout(image: Image.Image, target_size: int = 800):
    w, h = image.size
    scale_h = target_size / h
    scale_w = target_size / w
    resized = image.resize((target_size, target_size), Image.BILINEAR)
    arr = np.array(resized, dtype=np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    arr = (arr - mean) / std
    arr = arr.transpose(2, 0, 1)
    arr = np.expand_dims(arr, axis=0)
    return arr, scale_h, scale_w


def run_layout_detection(image: Image.Image):
    if layout_session is None:
        return []

    target_size = 800
    w, h = image.size
    input_tensor, scale_h, scale_w = preprocess_for_layout(image, target_size)

    input_names = [i.name for i in layout_session.get_inputs()]
    input_feed = {}
    for name in input_names:
        if "shape" in name or "im_shape" in name:
            input_feed[name] = np.array([[target_size, target_size]], dtype=np.float32)
        elif "scale" in name:
            input_feed[name] = np.array([[scale_h, scale_w]], dtype=np.float32)
        elif "image" in name or "img" in name:
            input_feed[name] = input_tensor

    outputs = layout_session.run(None, input_feed)
    results = []

    if len(outputs) >= 1:
        detections = outputs[0]
        if detections.ndim == 3:
            detections = detections[0]
        for det in detections:
            if len(det) < 6:
                continue
            cls_id, score, x1, y1, x2, y2 = det[:6]
            if score < LAYOUT_THRESHOLD:
                continue
            label = LAYOUT_LABELS.get(int(cls_id), f"unknown_{int(cls_id)}")
            if label in IGNORE_LABELS:
                continue
            results.append({
                "label": label, "score": float(score),
                "bbox": [float(x1), float(y1), float(x2), float(y2)],
            })

    results.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return results


def crop_region(image: Image.Image, bbox: list) -> Image.Image:
    w, h = image.size
    return image.crop((max(0, int(bbox[0])), max(0, int(bbox[1])),
                        min(w, int(bbox[2])), min(h, int(bbox[3]))))


def image_to_base64(image: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return f"data:image/{fmt.lower()};base64,{base64.b64encode(buf.getvalue()).decode()}"


async def call_vlm(image: Image.Image, prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if MLIS_API_KEY:
        headers["Authorization"] = f"Bearer {MLIS_API_KEY}"

    payload = {
        "model": VLM_MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_to_base64(image)}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 4096,
        "temperature": 0.0,
    }

    url = VLM_SERVER_URL.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions" if url.endswith("/v1") else f"{url}/v1/chat/completions"

    resp = await http_client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        logger.error(f"VLM error {resp.status_code}: {resp.text[:500]}")
        return f"[VLM Error: {resp.status_code}]"

    return resp.json()["choices"][0]["message"]["content"]


async def convert_to_pdf(file_bytes: bytes, filename: str) -> bytes:
    url = f"{GOTENBERG_URL}/forms/libreoffice/convert"
    resp = await http_client.post(url, files={"files": (filename, file_bytes)})
    if resp.status_code != 200:
        raise HTTPException(502, f"Gotenberg conversion failed: {resp.status_code}")
    return resp.content


async def process_page(image: Image.Image, page_num: int) -> dict:
    blocks = []
    if LAYOUT_ENABLED and layout_session is not None:
        detections = run_layout_detection(image)
        logger.info(f"Page {page_num}: {len(detections)} regions detected")
        for det in detections:
            region_img = crop_region(image, det["bbox"])
            prompt = LABEL_TO_PROMPT.get(det["label"], "OCR:")
            content = await call_vlm(region_img, prompt)
            blocks.append({"label": det["label"], "bbox": det["bbox"],
                           "score": det["score"], "content": content})
    else:
        content = await call_vlm(image, "OCR:")
        blocks.append({"label": "page", "bbox": [0, 0, image.width, image.height],
                        "score": 1.0, "content": content})
    return {"page": page_num, "blocks": blocks}


async def run_pipeline(file_bytes: bytes, filename: str) -> dict:
    start = time.time()
    ext = Path(filename).suffix.lower()

    if ext in CONVERT_EXTENSIONS:
        logger.info(f"Converting {filename} to PDF via Gotenberg...")
        pdf_bytes = await convert_to_pdf(file_bytes, filename)
    elif ext == ".pdf":
        pdf_bytes = file_bytes
    elif ext in {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}:
        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        result = await process_page(image, 1)
        elapsed = time.time() - start
        return {"filename": filename, "pages": [result],
                "markdown": blocks_to_markdown([result]),
                "elapsed_seconds": round(elapsed, 2)}
    else:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    logger.info("Converting PDF to page images...")
    images = convert_from_bytes(pdf_bytes, dpi=200, fmt="png")
    logger.info(f"Got {len(images)} pages")

    pages = []
    for i, img in enumerate(images):
        pages.append(await process_page(img, i + 1))

    markdown = blocks_to_markdown(pages)
    elapsed = time.time() - start
    return {"filename": filename, "pages": pages, "markdown": markdown,
            "elapsed_seconds": round(elapsed, 2)}


def blocks_to_markdown(pages: list) -> str:
    parts = []
    for page in pages:
        for block in page["blocks"]:
            label, content = block["label"], block["content"].strip()
            if not content:
                continue
            if label == "table":
                parts.append(f"\n{content}\n")
            elif label == "formula":
                parts.append(f"\n$$\n{content}\n$$\n")
            elif label in ("doc_title", "title"):
                parts.append(f"\n# {content}\n")
            elif label == "chart":
                parts.append(f"\n[Chart: {content}]\n")
            elif label == "seal":
                parts.append(f"\n[Seal: {content}]\n")
            else:
                parts.append(f"\n{content}\n")
    return "\n".join(parts)


@app.post("/ocr")
async def ocr_endpoint(file: UploadFile = File(...)):
    content = await file.read()
    result = await run_pipeline(content, file.filename or "document")
    return JSONResponse(result)


@app.put("/process")
async def openwebui_process(request: Request):
    body = await request.body()
    try:
        data = json.loads(body)
        if "file_path" in data:
            with open(data["file_path"], "rb") as f:
                file_bytes = f.read()
            filename = Path(data["file_path"]).name
        elif "content" in data:
            file_bytes = base64.b64decode(data["content"])
            filename = data.get("filename", "document.pdf")
        else:
            raise HTTPException(400, "Expected 'file_path' or 'content' in body")
    except (json.JSONDecodeError, UnicodeDecodeError):
        file_bytes = body
        filename = "document.pdf"

    result = await run_pipeline(file_bytes, filename)
    return JSONResponse({"content": result["markdown"],
                         "metadata": {"filename": result["filename"],
                                      "pages": len(result["pages"]),
                                      "elapsed_seconds": result["elapsed_seconds"]}})


@app.post("/process")
async def openwebui_process_post(request: Request):
    return await openwebui_process(request)


@app.get("/openapi.json")
async def custom_openapi():
    return {"openapi": "3.0.0",
            "info": {"title": "PaddleOCR-VL Pipeline Adapter", "version": "1.0.0"},
            "paths": {
                "/health": {"get": {"summary": "Health check"}},
                "/ocr": {"post": {"summary": "OCR a document (multipart file upload)"}},
                "/process": {"put": {"summary": "OpenWebUI External Loader"},
                             "post": {"summary": "OpenWebUI External Loader (POST)"}},
            }}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
