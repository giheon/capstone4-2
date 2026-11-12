"""What the tests share: the pipeline files, the profile hashes the Lab shows, and a workspace with a small
corpus and a clip in which the fixture stand-ins "find" people, text and plates."""
import av
import numpy as np
import pytest

from privframe.core.corpus import CorpusStore, Document, Unit
from privframe.core.fixture import Fixture, as_fixture
from privframe.core.pipeline import load_catalog, load_profile, resolve
from privframe.core.settings import ROOT, CoreSettings
from privframe.service.runner import RunManager

PIPELINE = ROOT / "configs" / "pipeline"
UNITS = [
    ("제2조", "제2조(정의)", "개인정보란 살아 있는 개인에 관한 정보로서 성명, 주민등록번호 및 영상 등을 통하여 개인을 알아볼 수 있는 정보를 말한다."),
    ("제17조", "제17조(개인정보의 제공)", "개인정보처리자는 정보주체의 동의를 받은 경우 정보주체의 개인정보를 제3자에게 제공할 수 있다. 동의 없는 제3자 얼굴 영상 공개에는 요건이 필요하다."),
]
# config_hash of every profile, as frontend/mock/pipeline.ts computes it
LAB_HASHES = {"baseline-v2": "e8a35ef0", "budget-saver": "eb07dc0a", "claude-api": "d383df07", "local-only": "8d72d9bc", "research-max": "475d4331"}


def make_clip(path, seconds=2, fps=12, size=(320, 240), tags=None) -> None:
    """A tiny clip with audio and no people; the fixture supplies the detections. `tags` are container tags."""
    with av.open(str(path), mode="w") as container:
        container.metadata.update(tags or {})
        video = container.add_stream("libx264", rate=fps)
        video.width, video.height, video.pix_fmt = size[0], size[1], "yuv420p"
        audio = container.add_stream("aac", rate=44100)
        rng = np.random.default_rng(0)
        base = rng.integers(40, 200, (size[1], size[0], 3), dtype=np.uint8)
        for index in range(seconds * fps):
            frame = av.VideoFrame.from_ndarray(np.roll(base, index * 2, axis=1), format="rgb24")
            for packet in video.encode(frame):
                container.mux(packet)
        tone = (np.sin(np.arange(44100 * seconds) * 2 * np.pi * 440 / 44100) * 0.2).astype(np.float32)
        for start in range(0, len(tone), 1024):
            chunk = av.AudioFrame.from_ndarray(tone[start : start + 1024].reshape(1, -1), format="fltp", layout="mono")
            chunk.sample_rate, chunk.pts = 44100, start
            for packet in audio.encode(chunk):
                container.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode():
                container.mux(packet)


@pytest.fixture
def workspace(tmp_path):
    store = CorpusStore(tmp_path / "data" / "corpus")
    record, _ = store.put_raw(doc_id="pipa", kind="law", title="개인정보 보호법", source="local_file", format="txt", content=b"x", citation="개인정보 보호법", authority=1)
    store.save_document(Document(record=record, parser="text", units=[Unit(*u) for u in UNITS]))
    clip = tmp_path / "clip.mp4"
    make_clip(clip)
    settings = CoreSettings(data_dir=tmp_path / "data", langsmith_api_key=None, openrouter_api_key=None)
    return settings, clip


def manager_for(settings, fixture=None, **params) -> RunManager:
    catalog = load_catalog(PIPELINE)

    def resolver(profile_id: str):
        profile = as_fixture(resolve(catalog, load_profile(PIPELINE, profile_id.removesuffix("-fixture"))))
        profile.params.update(params)
        return profile

    return RunManager(settings, fixture=fixture or Fixture(), profile_resolver=resolver)
