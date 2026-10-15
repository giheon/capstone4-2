"""Pinned vision assets, shared by adapters and the standalone downloader."""

YUNET_MODEL = {
    "name": "face_detection_yunet_2023mar.onnx",
    "version": "2023mar",
    "commit": "f12e12798e8314f7c074a6656816c048dcc95b7a",
    "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "size": 232589,
    "license": "MIT",
    "license_url": (
        "https://raw.githubusercontent.com/opencv/opencv_zoo/"
        "f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet/LICENSE"
    ),
    "url": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
        "f12e12798e8314f7c074a6656816c048dcc95b7a/"
        "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
}

ARCFACE_MODEL = {
    "name": "w600k_r50.onnx",
    "version": "buffalo_l-w600k-r50-v1",
    "sha256": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    "size": 174383860,
    "license": "non-commercial-research",
    "license_url": "https://github.com/deepinsight/insightface/tree/master/model_zoo",
    "url": "https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip",
    "archive_sha256": "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f",
    "archive_size": 288621354,
    "archive_member": "w600k_r50.onnx",
    "release_asset_id": 542726161,
}
