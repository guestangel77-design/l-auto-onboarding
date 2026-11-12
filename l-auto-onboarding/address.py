"""
L-Auto Onboarding - 우편번호 자동 조회 모듈
행정안전부 도로명주소 검색 API(business.juso.go.kr)로 도로명 주소의 우편번호(5자리)를 찾는다.
→ 실제 작동 (무료, .env의 JUSO_API_KEY 필요)
"""
import re

import requests

JUSO_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"


def clean_keyword(addr: str) -> str:
    """API가 거부하는 특수문자와 괄호 안 참고항목을 제거"""
    s = re.sub(r"\(.*?\)", " ", addr or "")
    s = re.sub(r"[%=><\[\]{}\"'`;|&*+^$\\]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def find_zip(api_key: str, road_address: str) -> dict:
    """
    반환: {"status": "ok"/"multi"/"none"/"error", "zip": "", "road": "", "message": "", "count": n}
    """
    if not api_key:
        return {"status": "error", "zip": "", "road": "", "count": 0,
                "message": "주소 API 키(JUSO_API_KEY)가 설정되지 않았습니다."}
    kw = clean_keyword(road_address)
    if len(kw) < 2:
        return {"status": "error", "zip": "", "road": "", "count": 0, "message": "도로명 주소가 비어 있습니다."}

    try:
        res = requests.get(JUSO_URL, params={
            "confmKey": api_key, "currentPage": 1, "countPerPage": 5,
            "keyword": kw, "resultType": "json",
        }, timeout=10)
        data = res.json()
    except requests.exceptions.Timeout:
        return {"status": "error", "zip": "", "road": "", "count": 0, "message": "주소 API 응답 시간이 초과되었습니다."}
    except requests.exceptions.ConnectionError:
        return {"status": "error", "zip": "", "road": "", "count": 0, "message": "주소 API에 연결할 수 없습니다. 사내망 차단 여부를 확인하세요."}
    except ValueError:
        return {"status": "error", "zip": "", "road": "", "count": 0, "message": "주소 API 응답을 읽을 수 없습니다."}

    results = data.get("results") or {}
    common = results.get("common") or {}
    code, msg = common.get("errorCode", ""), common.get("errorMessage", "")
    if code != "0":
        if code in ("E0001", "E0014", "E0015"):
            msg = f"주소 API 승인키 문제입니다. ({msg}) JUSO_API_KEY와 사용 기간을 확인하세요."
        return {"status": "error", "zip": "", "road": "", "count": 0, "message": msg or "주소 API 오류"}

    jusos = results.get("juso") or []
    if not jusos:
        return {"status": "none", "zip": "", "road": "", "count": 0,
                "message": "일치하는 주소가 없습니다. 도로명 주소를 확인하세요."}
    first = jusos[0]
    zip_no = first.get("zipNo", "")
    road = first.get("roadAddrPart1", "") or first.get("roadAddr", "")
    zips = {j.get("zipNo") for j in jusos}
    if len(zips) > 1:
        return {"status": "multi", "zip": zip_no, "road": road, "count": len(jusos),
                "message": f"검색 결과가 {len(jusos)}건입니다. 첫 번째 주소의 우편번호를 넣었으니 확인하세요."}
    return {"status": "ok", "zip": zip_no, "road": road, "count": len(jusos), "message": "우편번호를 찾았습니다."}
