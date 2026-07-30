"""실행 설정 값 객체. 만드는 쪽은 agent/config.py 의 parse_device_args()."""

from typing import NamedTuple


class RunConfig(NamedTuple):
    """실행 인자 + 환경 프리셋을 해석한 결과."""
    device: str
    stt_compute_type: str
    input_device_name: str | None
    input_device_index: int | None
    output_device_name: str | None
    output_device_index: int | None
    list_devices: bool
    debug_record: bool
    off_speaker: str | None
    barge_in_enabled: bool
    barge_in_threshold: float
