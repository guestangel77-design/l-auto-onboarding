"""
L-Auto Onboarding - ERP 입력용 변환 + 엑셀 만들기
화면 폼의 값을 ERP 구매처 생성요청 화면 순서대로 정리한다.
"""
import io
import re
from datetime import datetime

# 금융결제원 표준 은행코드 (ERP 코드표와 다르면 여기 수정)
BANK_CODES = [
    ("산업", "002"), ("기업", "003"), ("국민", "004"), ("수협", "007"), ("농협", "011"),
    ("우리", "020"), ("SC", "023"), ("제일", "023"), ("씨티", "027"), ("대구", "031"), ("iM", "031"),
    ("부산", "032"), ("광주", "034"), ("제주", "035"), ("전북", "037"), ("경남", "039"),
    ("새마을", "045"), ("신협", "048"), ("저축", "050"), ("우체국", "071"), ("하나", "081"),
    ("신한", "088"), ("케이뱅크", "089"), ("카카오", "090"), ("토스", "092"),
]


def digits(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def bank_code(name: str) -> str:
    n = re.sub(r"\s", "", name or "")
    if not n:
        return ""
    if "지역농" in n or "축협" in n:
        return "012"
    for key, code in BANK_CODES:
        if key in n:
            return code
    return ""


def mask_account(v: str) -> str:
    """계좌번호 가운데를 가린다: 110-123-456789 → 110-***-**6789"""
    d = digits(v)
    if len(d) < 7:
        return "*" * len(d)
    head, tail = d[:3], d[-4:]
    return f"{head}-{'*' * (len(d) - 7)}-{tail}" if len(d) > 7 else f"{head}-{tail}"


def build_rows(v: dict) -> list:
    """
    v: 폼 값 dict. 반환: [ [그룹명] 또는 [항목명, 값, 비었을 때 안내, 계좌여부] ]
    """
    g = lambda k: (v.get(k) or "").strip()
    is_corp = len(digits(g("corp_no"))) == 13
    birth = digits(g("birth_date"))
    bname = g("bank_name")
    bcode = bank_code(bname)
    return [
        ["업체 일반정보"],
        ["업체명(성명)", g("trade_name"), "", False],
        ["사업자 유형", "01(법인)" if is_corp else "02(개인사업자)", "", False],
        ["주민등록번호", birth + "0000000" if len(birth) == 6 else "", "대표자 생년월일 6자리를 위 폼에 입력하세요", False],
        ["사업자등록번호", digits(g("biz_no")), "", False],
        ["법인등록번호", digits(g("corp_no")), "" if is_corp else "해당 없음 (개인사업자)", False],
        ["대표자명", g("ceo_name"), "", False],
        ["업태", g("biz_type"), "", False],
        ["종목", g("biz_item"), "", False],
        ["국가", "KR", "", False],
        ["우편번호", g("zip_code"), "「우편번호 찾기」로 조회하거나 직접 입력", False],
        ["도로명 주소", g("address_road"), "", False],
        ["상세주소(동이하)", g("address_detail"), "", False],
        ["은행계좌정보"],
        ["은행코드", bcode, f"코드 확인 필요: {bname}" if bname else "", False],
        ["은행명", bname, "", False],
        ["계좌번호", digits(g("account_no")), "", True],
        ["예금주", g("account_holder"), "", False],
    ]


def approval_comment(checks: list) -> str:
    """결재 상신 의견란에 붙여넣을 문장"""
    if not checks:
        return ""
    icon = {"pass": "적합", "warn": "주의", "fail": "부적합", "error": "확인불가", "skip": "생략"}
    parts = []
    for c in checks:
        mock = "(시연용 DB)" if c.get("mode") == "mock" else ""
        parts.append(f"{c['title']}{mock} {icon.get(c['status'], '')}: {c['message']}")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"[L-Auto 사전검증 {stamp}] " + " / ".join(parts)


def make_excel(rows: list, checks: list, verdict: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "ERP 입력용"
    ws.append(["항목", "값"])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDE7F3")
    for r in rows:
        if len(r) == 1:
            ws.append([f"■ {r[0]}"])
            ws.cell(ws.max_row, 1).font = Font(bold=True)
        else:
            ws.append([r[0], r[1]])
            ws.cell(ws.max_row, 2).number_format = "@"  # 숫자 앞 0이 사라지지 않게 텍스트로
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 50

    ws2 = wb.create_sheet("사전검증 결과")
    ws2.append(["검증 항목", "결과", "구분", "내용"])
    for c in ws2[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDE7F3")
    label = {"pass": "통과", "warn": "주의", "fail": "불가", "error": "확인 불가", "skip": "생략"}
    for c in checks:
        ws2.append([c["title"], label.get(c["status"], c["status"]),
                    "시연용 Mock" if c.get("mode") == "mock" else "실제 작동", c["message"]])
    ws2.append([])
    ws2.append(["최종 판정", verdict.get("message", "")])
    ws2.append(["결재 의견", approval_comment(checks)])
    ws2.cell(ws2.max_row, 2).alignment = Alignment(wrap_text=True)
    for col, w in zip("ABCD", (34, 10, 12, 70)):
        ws2.column_dimensions[col].width = w

    for sheet in (ws, ws2):
        sheet.append([])
        sheet.append(["※ 가상 데이터 기반 시연 결과물"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
