import json
import sys

# Đọc nội dung từ train_colab.py bằng cách import các biến
from train_colab import (
    SECTION_0, SECTION_1, SECTION_2, SECTION_3,
    SECTION_4, SECTION_5, SECTION_6, SECTION_7,
    SECTION_8, SECTION_9, SECTION_10
)

sections = [
    ("Cài đặt thư viện (chạy 1 lần đầu tiên)", SECTION_0),
    ("Tải font chữ Trung + cài apt packages", SECTION_1),
    ("Tải COCO val2017 làm background", SECTION_2),
    ("Định nghĩa hàm tạo dataset", SECTION_3),
    ("Sinh toàn bộ dataset", SECTION_4),
    ("Tạo data_v2.yaml", SECTION_5),
    ("Huấn luyện YOLOv8s", SECTION_6),
    ("Đánh giá model", SECTION_7),
    ("Visualize inference", SECTION_8),
    ("Export ONNX + Download", SECTION_9),
    ("Hướng dẫn cập nhật pipeline.py", SECTION_10)
]

cells = []

# Cell giới thiệu
cells.append({
    "cell_type": "markdown",
    "metadata": {},
    "source": [
        "# 🀄 Train YOLOv8 – Nhận Diện Chữ Trung Quốc (v2 – Anti False-Positive)\n",
        "\n",
        "## Cải tiến trong bản v2:\n",
        "- **Thêm Negative samples (25%)**: Ảnh cảnh thuần để dạy model cách TỪ CHỐI false positive.\n",
        "- **Thêm Hard negative (15%)**: Chữ Latin/số để tránh nhầm lẫn.\n",
        "- **Background thực tế**: Dùng 5000 ảnh từ COCO val2017 thay vì nền gradient đơn giản.\n",
        "- **Augmentation video thực**: JPEG compression, motion blur, noise...\n",
        "- **Mô hình lớn hơn**: Dùng `yolov8s` thay vì `yolov8n` (+10% mAP).\n",
        "- **Hyperparameters tối ưu**: Tăng `cls` loss để phân biệt class tốt hơn, tăng `conf` threshold để giảm false positive.\n",
        "\n",
        "## Hướng dẫn sử dụng:\n",
        "1. Chọn **Runtime → Change runtime type → T4 GPU**\n",
        "2. Chạy lần lượt các cell bên dưới từ trên xuống dưới."
    ]
})

for i, (title, code) in enumerate(sections):
    # Tiêu đề section
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": [f"### SECTION {i}: {title}"]
    })
    
    # Chuyển text thành danh sách string có kèm \n cho đúng chuẩn Jupyter (nhưng thực tế string liền cũng được nếu file JSON)
    cells.append({
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": code.strip().splitlines(True)  # Giữ lại \n ở cuối mỗi dòng
    })

notebook = {
    "cells": cells,
    "metadata": {
        "accelerator": "GPU",
        "colab": {
            "gpuType": "T4",
            "provenance": []
        },
        "kernelspec": {
            "display_name": "Python 3",
            "name": "python3"
        },
        "language_info": {
            "name": "python"
        }
    },
    "nbformat": 4,
    "nbformat_minor": 0
}

output_file = "train_colab_v2.ipynb"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(notebook, f, ensure_ascii=False, indent=2)

print(f"Đã tạo file {output_file} thành công!")
