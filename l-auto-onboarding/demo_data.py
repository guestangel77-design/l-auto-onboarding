"""
L-Auto Onboarding - 시연용 샘플 결과 (🟡 Mock)
Claude API 키가 없는 환경(예: 심사위원이 소스를 내려받아 바로 실행)에서도
samples 폴더의 가상 서류 2세트로 전체 흐름을 체험할 수 있게, 미리 정리해 둔 인식 결과를 돌려준다.
※ 키가 있으면 이 파일은 쓰지 않고 실제 Claude Vision으로 읽는다.
"""
import hashlib
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE_DIR, "samples")

SAMPLE_SETS = {
    "sample1": {
        "label": "샘플1 · 정상 법인 (가나다푸드)",
        "biz_file": "sample1_ok_biz.png",
        "bank_file": "sample1_ok_bank.png",
        "biz": {
            "trade_name": "주식회사 가나다푸드",
            "biz_no": "123-45-67891",
            "ceo_name": "홍길동",
            "open_date": "2021-03-15",
            "corp_no": "110111-1234567",
            "biz_type": "도소매, 음식점업",
            "biz_item": "식품, 디저트 카페",
            "address": "부산광역시 중구 테스트로 123, 4층 (가상동)",
            "address_road": "부산광역시 중구 테스트로 123",
            "address_detail": "4층 (가상동)",
            "birth_date": "",
        },
        "bank": {
            "bank_name": "가상은행",
            "account_no": "110-123-456789",
            "account_holder": "주식회사 가나다푸드",
        },
    },
    "sample2": {
        "label": "샘플2 · 문제 개인사업자 (라마바 스튜디오)",
        "biz_file": "sample2_bad_biz.png",
        "bank_file": "sample2_bad_bank.png",
        "biz": {
            "trade_name": "라마바 스튜디오",
            "biz_no": "210-12-34565",
            "ceo_name": "김가상",
            "open_date": "2019-07-01",
            "corp_no": "",
            "biz_type": "서비스업",
            "biz_item": "교육, 공예 강습",
            "address": "부산광역시 중구 샘플길 45, 2층 (가상동)",
            "address_road": "부산광역시 중구 샘플길 45",
            "address_detail": "2층 (가상동)",
            "birth_date": "",
        },
        "bank": {
            "bank_name": "테스트은행",
            "account_no": "301-2345-6789-01",
            "account_holder": "이다름",
        },
    },
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_sample(set_id: str, kind: str) -> tuple:
    """(파일 내용 bytes, 파일명) 반환. kind = 'biz' 또는 'bank'"""
    name = SAMPLE_SETS[set_id][f"{kind}_file"]
    with open(os.path.join(SAMPLE_DIR, name), "rb") as f:
        return f.read(), name


def find_demo_result(biz_bytes: bytes, bank_bytes: bytes):
    """올라온 두 파일이 같은 샘플 세트면 미리 정리한 결과를 돌려준다. 아니면 None"""
    for set_id, s in SAMPLE_SETS.items():
        try:
            biz, _ = read_sample(set_id, "biz")
            bank, _ = read_sample(set_id, "bank")
        except OSError:
            continue
        if _sha(biz) == _sha(biz_bytes) and _sha(bank) == _sha(bank_bytes):
            return {"biz": dict(s["biz"]), "bank": dict(s["bank"]), "warnings": [],
                    "elapsed": 0.0, "model": "시연용 샘플 결과", "set_id": set_id}
    return None
