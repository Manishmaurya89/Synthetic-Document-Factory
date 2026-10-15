import json
import os

import pypdfium2 as pdfium
from PIL import ImageDraw, ImageFont

# Same names and ids as DocLayNet (COCO ids 1–11), so the output can be mixed
# with DocLayNet or used with models trained on it. Footnote and Formula are
# never produced but keep the ids aligned.
CATEGORIES = [
    "Caption", "Footnote", "Formula", "List-item", "Page-footer", "Page-header",
    "Picture", "Section-header", "Table", "Text", "Title",
]
CATEGORY_ID = {name: i + 1 for i, name in enumerate(CATEGORIES)}

PREVIEW_COLORS = {
    "Caption": "#e67e22", "Footnote": "#7f8c8d", "Formula": "#7f8c8d", "List-item": "#16a085",
    "Page-footer": "#95a5a6", "Page-header": "#95a5a6", "Picture": "#8e44ad",
    "Section-header": "#c0392b", "Table": "#2980b9", "Text": "#27ae60", "Title": "#d35400",
}


def _preview_font():
    try:
        return ImageFont.load_default(size=16)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def export_dataset(out_dir: str, renders: list, dpi: int = 150, preview: bool = False) -> dict:
    """Turn rendered PDFs + recorded boxes into a layout-detection dataset:

    images/<doc>_pNN.png   one image per page
    labels/<doc>_pNN.txt   YOLO boxes (class cx cy w h, normalised 0–1)
    annotations.json       COCO boxes (pixels) with the text of each element
    data.yaml              ready for `yolo train data=data.yaml`
    preview/<doc>_pNN.png  pages with the boxes drawn on (only with preview=True)
    """
    images_dir = os.path.join(out_dir, "images")
    labels_dir = os.path.join(out_dir, "labels")
    preview_dir = os.path.join(out_dir, "preview")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(labels_dir, exist_ok=True)
    if preview:
        os.makedirs(preview_dir, exist_ok=True)

    coco = {
        "info": {"description": "Synthetic Document Factory layout dataset", "version": "1.0"},
        "licenses": [],
        "images": [],
        "annotations": [],
        "categories": [{"id": CATEGORY_ID[n], "name": n, "supercategory": "layout"} for n in CATEGORIES],
    }
    counts = {}

    for render in renders:
        pdf = pdfium.PdfDocument(render["pdf"])
        try:
            for index in range(len(pdf)):
                page_no = index + 1
                page = pdf[index]
                page_w, page_h = page.get_size()
                image = page.render(scale=dpi / 72).to_pil().convert("RGB")
                page.close()

                name = f"{render['name']}_p{page_no:02d}"
                image_id = len(coco["images"]) + 1
                coco["images"].append({
                    "id": image_id, "file_name": f"{name}.png",
                    "width": image.width, "height": image.height,
                    "doc_name": render["name"], "page_no": page_no,
                })

                # PDF points (origin bottom-left) to image pixels (origin top-left)
                sx, sy = image.width / page_w, image.height / page_h
                pixel_boxes = []
                for b in render["boxes"]:
                    if b["page"] != page_no:
                        continue
                    x0 = max(0.0, b["x0"] * sx)
                    x1 = min(float(image.width), b["x1"] * sx)
                    y0 = max(0.0, (page_h - b["y1"]) * sy)
                    y1 = min(float(image.height), (page_h - b["y0"]) * sy)
                    if x1 - x0 >= 1 and y1 - y0 >= 1:
                        pixel_boxes.append((b["label"], x0, y0, x1, y1, b.get("text", "")))

                yolo = []
                for label, x0, y0, x1, y1, text in pixel_boxes:
                    w, h = x1 - x0, y1 - y0
                    cat = CATEGORY_ID[label]
                    coco["annotations"].append({
                        "id": len(coco["annotations"]) + 1, "image_id": image_id, "category_id": cat,
                        "bbox": [round(x0, 2), round(y0, 2), round(w, 2), round(h, 2)],
                        "area": round(w * h, 2), "iscrowd": 0,
                        "segmentation": [[round(v, 2) for v in (x0, y0, x1, y0, x1, y1, x0, y1)]],
                        "text": text,
                    })
                    yolo.append(f"{cat - 1} {(x0 + w / 2) / image.width:.6f} {(y0 + h / 2) / image.height:.6f} "
                                f"{w / image.width:.6f} {h / image.height:.6f}")
                    counts[label] = counts.get(label, 0) + 1

                image.save(os.path.join(images_dir, f"{name}.png"))
                with open(os.path.join(labels_dir, f"{name}.txt"), "w") as f:
                    f.write("\n".join(yolo) + ("\n" if yolo else ""))

                if preview:
                    draw = ImageDraw.Draw(image)
                    font = _preview_font()
                    for label, x0, y0, x1, y1, _ in pixel_boxes:
                        color = PREVIEW_COLORS[label]
                        draw.rectangle([x0, y0, x1, y1], outline=color, width=3)
                        draw.text((x0 + 4, max(0, y0 - 18)), label, fill=color, font=font)
                    image.save(os.path.join(preview_dir, f"{name}.png"))
        finally:
            pdf.close()

    with open(os.path.join(out_dir, "annotations.json"), "w", encoding="utf-8") as f:
        json.dump(coco, f, ensure_ascii=False, indent=1)

    with open(os.path.join(out_dir, "data.yaml"), "w") as f:
        f.write(f"path: {os.path.abspath(out_dir)}\ntrain: images\nval: images\nnames:\n")
        for i, name in enumerate(CATEGORIES):
            f.write(f"  {i}: {name}\n")

    return {"pages": len(coco["images"]), "boxes": len(coco["annotations"]), "counts": counts}
