"""
L-Auto Onboarding - 구매처 등록 서류 자동 인식 · 사전 검증 도구 (Streamlit)
실행: python -m streamlit run app.py
"""
import base64
import html
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 같은 폴더의 모듈을 확실히 찾도록

import streamlit as st

from address import find_zip
from demo_data import DEMO_NTS, SAMPLE_SETS, find_demo_result, read_sample
from doc_parser import ParseError, detect_mime, parse_documents
from erp import approval_comment, build_rows, make_excel, mask_account
from verifier import run_all

VERSION = "v5.2 (Streamlit)"
MAX_FILE_MB = 10
MAX_PARSE_PER_SESSION = 20  # 공개 URL 비용 보호: 한 사람이 한 번 접속해서 AI를 부를 수 있는 횟수

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------
# 0. 설정: API 키는 코드에 쓰지 않는다
#    순서: Streamlit Secrets(호스팅) → 환경변수 → .env 파일(내 PC)
# ---------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass


def secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:  # secrets 파일이 없으면 오류가 나므로 무시
        pass
    return os.getenv(name, default).strip()


def cfg() -> dict:
    db = secret("SEIZURE_DB_PATH") or "samples/seizure_db.csv"
    db = db if os.path.isabs(db) else os.path.join(BASE_DIR, db)
    if not os.path.exists(db):
        db = os.path.join(BASE_DIR, "samples", "seizure_db.csv")
    return {
        "ai_key": secret("ANTHROPIC_API_KEY"),
        "model": secret("CLAUDE_MODEL", "claude-sonnet-5-5") or "claude-sonnet-5-5",
        "nts_key": secret("NTS_API_KEY"),
        "juso_key": secret("JUSO_API_KEY"),
        "seizure_db": db,
        "seizure_mode": "mock" if os.path.dirname(db) == os.path.join(BASE_DIR, "samples") else "real",
    }


C = cfg()

# ---------------------------------------------------------------
# 1. 화면 항목 정의
# ---------------------------------------------------------------
BIZ_FIELDS = [
    ("trade_name", "상호(법인명)", False),
    ("biz_no", "사업자등록번호", False),
    ("ceo_name", "대표자명", False),
    ("birth_date", "대표자 생년월일 6자리 (서류에 없으면 직접 입력)", True),
    ("open_date", "개업연월일 (YYYY-MM-DD)", False),
    ("corp_no", "법인등록번호 (법인만)", True),
    ("biz_type", "업태", False),
    ("biz_item", "종목", False),
    ("zip_code", "우편번호", True),
    ("address_road", "도로명 주소", False),
    ("address_detail", "상세 주소", True),
]
BANK_FIELDS = [
    ("bank_name", "은행명", False),
    ("account_holder", "예금주", False),
    ("account_no", "계좌번호", False),
]
ALL_KEYS = [k for k, _, _ in BIZ_FIELDS + BANK_FIELDS]

st.set_page_config(page_title="L-Auto Onboarding", page_icon="📄", layout="wide")

ss = st.session_state
ss.setdefault("docs", {})          # {"biz": (bytes, 파일명), "bank": (...)}
ss.setdefault("uploader_ver", 0)   # 샘플을 고르면 업로드 칸을 비우기 위한 번호
ss.setdefault("parse_meta", None)  # 처리 시간, 모드, 경고
ss.setdefault("verify", None)      # 검증 결과
ss.setdefault("verify_stale", False)
ss.setdefault("parse_count", 0)
ss.setdefault("zip_msg", None)
for k in ALL_KEYS:
    ss.setdefault(f"f_{k}", "")


def form_values() -> dict:
    return {k: ss.get(f"f_{k}", "") for k in ALL_KEYS}


def do_verify():
    v = form_values()
    biz = {k: v[k] for k, _, _ in BIZ_FIELDS}
    bank = {"account_holder": v["account_holder"]}  # 계좌번호는 검증 서버로 보내지 않는다
    ss.verify = run_all(C["nts_key"], C["seizure_db"], biz, bank, C["seizure_mode"], demo_nts=DEMO_NTS)
    ss.verify_stale = False


def lookup_zip():
    r = find_zip(C["juso_key"], ss.f_address_road)
    if r["zip"]:
        ss.f_zip_code = r["zip"]
    ss.zip_msg = r


def mark_stale():
    if ss.verify:
        ss.verify_stale = True


def load_sample(set_id: str):
    ss.docs = {"biz": read_sample(set_id, "biz"), "bank": read_sample(set_id, "bank")}
    ss.uploader_ver += 1
    ss.parse_meta, ss.verify = None, None


def clear_all():
    ss.docs = {}
    ss.uploader_ver += 1
    ss.parse_meta, ss.verify, ss.zip_msg = None, None, None
    for k in ALL_KEYS:
        ss[f"f_{k}"] = ""


# ---------------------------------------------------------------
# 2. 머리글 · 보안 안내 · 연결 상태
# ---------------------------------------------------------------
st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
.badge {display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; margin:2px 4px 2px 0; border:1px solid #d0d5dd;}
.b-ok {background:#ecfdf3; color:#067647; border-color:#abefc6;}
.b-mock {background:#fffaeb; color:#b54708; border-color:#fedf89;}
.b-off {background:#f2f4f7; color:#475467;}
.secure {background:#eff8ff; border:1px solid #b2ddff; color:#175cd3; padding:8px 12px; border-radius:8px; font-size:13px; margin:6px 0 4px;}
.card {border:1px solid #eaecf0; border-left-width:6px; border-radius:8px; padding:10px 14px; margin-bottom:8px;}
.card .t {font-weight:700;} .card .m {font-size:14px; margin-top:2px;} .card .d {font-size:12px; color:#667085; margin-top:2px;}
.s-pass {border-left-color:#12b76a;} .s-warn {border-left-color:#f79009;} .s-fail {border-left-color:#f04438;}
.s-error {border-left-color:#98a2b3;} .s-skip {border-left-color:#d0d5dd;}
.verdict {padding:14px 18px; border-radius:10px; font-size:18px; font-weight:800; margin:8px 0;}
.v-ok {background:#ecfdf3; color:#067647;} .v-warn {background:#fffaeb; color:#b54708;} .v-no {background:#fef3f2; color:#b42318;}
</style>
""", unsafe_allow_html=True)

st.title("📄 L-Auto Onboarding")
st.caption(f"사업자등록증·통장사본을 올리면 AI가 구매처 등록 폼을 채우고, 결재 상신 전에 자동으로 검증합니다. · {VERSION}")


def badge(text, kind):
    return f'<span class="badge b-{kind}">{text}</span>'


badges = [
    badge("🟢 서류 인식: Claude Vision", "ok") if C["ai_key"] else badge("🟡 서류 인식: 시연 모드(키 없음 · 샘플만)", "mock"),
    badge("🟢 국세청 API", "ok") if C["nts_key"] else badge("⚪ 국세청 API 키 없음", "off"),
    badge("🟢 우편번호 API", "ok") if C["juso_key"] else badge("⚪ 우편번호 API 키 없음", "off"),
    badge("🟡 압류 DB: 시연용 가상 목록", "mock") if C["seizure_mode"] == "mock" else badge("🟢 압류 DB: 준법지원팀 파일", "ok"),
]
st.markdown(" ".join(badges), unsafe_allow_html=True)
st.markdown(
    '<div class="secure">🔒 <b>가상 데이터 전용 시연 도구입니다. 실제 거래처 서류는 올리지 마세요.</b> '
    "올린 서류는 저장하지 않고, 항목 인식을 위해 Claude API로만 전송됩니다. "
    "계좌번호는 화면에서 가려 보여주며 검증 서버로 보내지 않습니다.</div>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------
# 3. ① 서류 올리기
# ---------------------------------------------------------------
st.subheader("① 서류 올리기")

s1, s2, s3 = st.columns([1, 1, 1])
s1.button("🧪 샘플1로 체험 (정상 법인)", on_click=load_sample, args=("sample1",), width="stretch")
s2.button("🧪 샘플2로 체험 (문제 업체)", on_click=load_sample, args=("sample2",), width="stretch")
s3.button("↺ 처음부터", on_click=clear_all, width="stretch")

col_biz, col_bank = st.columns(2)


def uploader(col, kind, label):
    with col:
        f = st.file_uploader(f"{label} (JPG·PNG·PDF, {MAX_FILE_MB}MB 이하)",
                             type=["jpg", "jpeg", "png", "webp", "pdf"],
                             key=f"up_{kind}_{ss.uploader_ver}")
        if f is not None:
            data = f.getvalue()
            if len(data) > MAX_FILE_MB * 1024 * 1024:
                st.error(f"{label} 파일이 {MAX_FILE_MB}MB를 넘습니다.")
            elif not detect_mime(data):
                st.error(f"{label}: JPG, PNG, WEBP, PDF 파일만 올릴 수 있습니다.")
            else:
                ss.docs[kind] = (data, f.name)
        doc = ss.docs.get(kind)
        if doc:
            data, name = doc
            if detect_mime(data) == "application/pdf":
                st.info(f"📎 {name} (PDF)")
            else:
                st.image(data, caption=name, width="stretch")


uploader(col_biz, "biz", "사업자등록증")
uploader(col_bank, "bank", "통장사본")

ready = "biz" in ss.docs and "bank" in ss.docs
run = st.button("🤖 AI로 자동 추출하고 검증하기", type="primary", disabled=not ready, width="stretch")
if not ready:
    st.caption("서류 2종을 올리거나 위의 샘플 버튼을 누르세요.")

if run:
    biz_bytes, bank_bytes = ss.docs["biz"][0], ss.docs["bank"][0]
    t0 = time.time()
    result, mode, error = None, "real", None
    if not C["ai_key"]:
        result = find_demo_result(biz_bytes, bank_bytes)
        mode = "demo"
        if result is None:
            error = "AI 키가 설정되지 않아 직접 올린 서류는 읽을 수 없습니다. 샘플 버튼으로 체험하세요."
    elif ss.parse_count >= MAX_PARSE_PER_SESSION:
        error = f"한 번 접속에 AI 인식은 {MAX_PARSE_PER_SESSION}회까지 가능합니다. 새로고침 후 다시 시도하세요."
    else:
        try:
            with st.spinner("AI가 서류를 읽고 있어요... (10~20초)"):
                b64 = lambda d: (base64.b64encode(d).decode(), detect_mime(d))
                result = parse_documents(C["ai_key"], C["model"], b64(biz_bytes), b64(bank_bytes))
            ss.parse_count += 1
        except ParseError as e:
            error = str(e)
        except Exception:
            error = "처리 중 오류가 발생했습니다. 다시 시도해 주세요."

    if error:
        st.error(error)
    else:
        for k in ALL_KEYS:
            ss[f"f_{k}"] = (result["biz"].get(k) or result["bank"].get(k) or "") if k != "zip_code" else ""
        ss.zip_msg = None
        if C["juso_key"] and ss.f_address_road:
            lookup_zip()
        with st.spinner("국세청 조회와 사전 검증 중..."):
            do_verify()
        filled = sum(1 for k in ALL_KEYS if ss[f"f_{k}"].strip())
        ss.parse_meta = {"mode": mode, "warnings": result.get("warnings", []),
                         "ai_sec": result.get("elapsed", 0), "total_sec": round(time.time() - t0, 1),
                         "filled": filled}

# ---------------------------------------------------------------
# 4. ② 인식 결과 (직접 수정 가능)
# ---------------------------------------------------------------
if ss.parse_meta:
    m = ss.parse_meta
    st.divider()
    st.subheader("② 인식 결과 — 틀린 값은 바로 고칠 수 있어요")
    if m["mode"] == "demo":
        st.warning("🟡 시연 모드: AI 키가 없어 샘플 서류의 미리 정리된 인식 결과를 보여줍니다. "
                   "(키를 설정하면 실제 Claude Vision으로 읽습니다)")
    for w in m["warnings"]:
        st.warning(w)

    k1, k2, k3 = st.columns(3)
    k1.metric("자동 입력 항목", f"{m['filled']} / {len(ALL_KEYS)}")
    k2.metric("처리 시간 (업로드 → 판정)", f"{m['total_sec']}초")
    k3.metric("기존 수기 입력", "약 15분", delta=f"-{max(0, 900 - m['total_sec']):.0f}초", delta_color="inverse")

    c_biz, c_bank = st.columns([3, 2])
    with c_biz:
        st.markdown("**사업자등록증**")
        for key, label, optional in BIZ_FIELDS:
            if key == "zip_code":
                zc1, zc2 = st.columns([2, 1])
                zc1.text_input(label, key="f_zip_code", on_change=mark_stale)
                zc2.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                zc2.button("🔎 우편번호 찾기", on_click=lookup_zip, disabled=not C["juso_key"], width="stretch")
                if ss.zip_msg:
                    (st.caption if ss.zip_msg["status"] == "ok" else st.warning)(ss.zip_msg["message"])
                continue
            st.text_input(label + (" (해당 시)" if optional else ""), key=f"f_{key}", on_change=mark_stale)
    with c_bank:
        st.markdown("**통장사본**")
        for key, label, _ in BANK_FIELDS:
            if key == "account_no":
                show = st.toggle("계좌번호 전체 보기", value=False)
                st.text_input(label, key="f_account_no", type="default" if show else "password", on_change=mark_stale)
                st.caption(f"화면 표시: {mask_account(ss.f_account_no) or '-'}")
            else:
                st.text_input(label, key=f"f_{key}", on_change=mark_stale)

    # -----------------------------------------------------------
    # 5. ③ 상신 전 사전 검증
    # -----------------------------------------------------------
    st.divider()
    h1, h2 = st.columns([3, 1])
    h1.subheader("③ 상신 전 사전 검증")
    if h2.button("🔁 검증 다시 실행", width="stretch"):
        with st.spinner("다시 검증 중..."):
            do_verify()

    v = ss.verify
    if v:
        if ss.verify_stale:
            st.info("값을 고쳤어요. 「검증 다시 실행」을 눌러 주세요.")
        icon = {"pass": "✅", "warn": "⚠️", "fail": "⛔", "error": "❔", "skip": "➖"}
        for c in v["checks"]:
            no_key = c["status"] == "error" and "설정되지 않았습니다" in c["message"]
            tag = "🟡 시연용 Mock" if c.get("mode") == "mock" else ("⚪ 키 없음" if no_key else "🟢 실제 작동")
            tag_cls = "mock" if c.get("mode") == "mock" else ("off" if no_key else "ok")
            detail = " · ".join(f"{k}: {html.escape(str(val))}" for k, val in (c.get("detail") or {}).items())
            st.markdown(
                f'<div class="card s-{c["status"]}"><div class="t">{icon.get(c["status"], "")} {html.escape(c["title"])} '
                f'<span class="badge b-{tag_cls}">{tag}</span></div>'
                f'<div class="m">{html.escape(c["message"])}</div>'
                + (f'<div class="d">{detail}</div>' if detail else "") + "</div>",
                unsafe_allow_html=True,
            )
        has_warn = any(c["status"] == "warn" for c in v["checks"])
        cls = "v-no" if not v["verdict"]["ok"] else ("v-warn" if has_warn else "v-ok")
        mark = "🔴" if cls == "v-no" else ("🟡" if cls == "v-warn" else "🟢")
        st.markdown(f'<div class="verdict {cls}">{mark} {html.escape(v["verdict"]["message"])}</div>',
                    unsafe_allow_html=True)
        if any(c["key"].startswith("nts") and c.get("mode") == "mock" for c in v["checks"]):
            st.caption("ℹ️ 샘플 서류의 사업자번호는 가상 번호라서 국세청 3개 항목은 🟡 시연용 결과를 보여줍니다. "
                       "다른 사업자번호를 입력하고 「검증 다시 실행」을 누르면 실제 국세청 API로 조회합니다.")

    # -----------------------------------------------------------
    # 6. ④ ERP 입력용
    # -----------------------------------------------------------
    st.divider()
    st.subheader("④ ERP 입력용 — 칸별 [복사] 버튼으로 붙여넣기")
    if v and not ss.verify_stale:
        if v["verdict"]["ok"]:
            st.success("사전 검증을 통과했습니다. 아래 값을 ERP 구매처 생성요청에 붙여넣으세요.")
        else:
            st.error("사전 검증 미통과 상태입니다. 문제를 해결한 뒤 상신하세요.")

    rows = build_rows(form_values())
    table_rows = []
    for r in rows:
        if len(r) == 1:
            table_rows.append({"group": r[0]})
        else:
            label, value, empty_msg, secret_row = r
            table_rows.append({"label": label, "value": value, "empty": empty_msg,
                               "shown": mask_account(value) if secret_row else value, "secret": secret_row})
    n_rows = len(table_rows)
    erp_html = f"""
<style>
body {{margin:0; font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;}}
table {{width:100%; border-collapse:collapse; font-size:14px;}}
th {{text-align:left; background:#f2f6fb; color:#344054; font-weight:600; width:34%; padding:7px 10px; border:1px solid #eaecf0;}}
td {{padding:6px 10px; border:1px solid #eaecf0; word-break:break-all;}}
td.need {{color:#b54708;}}
td.c {{width:120px; text-align:center; white-space:nowrap;}}
tr.g th {{background:#e8eef7; color:#1f2328; font-weight:800;}}
button {{border:1px solid #d0d5dd; background:#fff; border-radius:6px; padding:3px 10px; font-size:12px; cursor:pointer;}}
button.done {{background:#ecfdf3; color:#067647; border-color:#abefc6;}}
</style>
<table id="t"></table>
<script>
const rows = {json.dumps(table_rows, ensure_ascii=False)};
const esc = s => String(s).replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[c]);
document.getElementById("t").innerHTML = rows.map((r, i) => r.group
  ? `<tr class="g"><th colspan="3">${{esc(r.group)}}</th></tr>`
  : `<tr><th>${{esc(r.label)}}</th><td class="${{r.value ? "" : "need"}}">${{esc(r.value ? r.shown : r.empty)}}</td>
     <td class="c">${{r.value && r.secret ? `<button class="peek" data-i="${{i}}">보기</button> ` : ""}}${{r.value ? `<button class="cp" data-i="${{i}}">복사</button>` : ""}}</td></tr>`).join("");
document.querySelectorAll("button.peek").forEach(b => b.addEventListener("click", () => {{
  const r = rows[+b.dataset.i], cell = b.closest("tr").children[1];
  const open = b.textContent === "보기";
  cell.textContent = open ? r.value : r.shown;
  b.textContent = open ? "가리기" : "보기";
}}));
document.querySelectorAll("button.cp").forEach(b => b.addEventListener("click", async () => {{
  const v = rows[+b.dataset.i].value;
  try {{ await navigator.clipboard.writeText(v); }}
  catch (e) {{ const t = document.createElement("textarea"); t.value = v; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }}
  b.textContent = "복사됨"; b.classList.add("done");
  setTimeout(() => {{ b.textContent = "복사"; b.classList.remove("done"); }}, 1200);
}}));
</script>
"""
    st.iframe(erp_html, height=36 * n_rows + 10)
    st.caption("회사코드·계정그룹·담당자·지급·회계 정보는 서류에 없으므로 ERP에서 직접 입력합니다. "
               "계좌번호는 가려서 보여주며 [보기]로 잠깐 확인할 수 있고, [복사]는 전체 번호를 복사합니다.")

    if v:
        st.markdown("**결재 상신 의견 (자동 작성)**")
        st.code(approval_comment(v["checks"]), language=None, wrap_lines=True)
        name = (ss.f_trade_name or "구매처").replace("/", "_").replace("\\", "_")
        st.download_button(
            "📥 ERP 입력용 엑셀 내려받기 (검증 결과 포함)",
            data=make_excel(rows, v["checks"], v["verdict"]),
            file_name=f"{name}_구매처등록_가상데이터.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )

st.divider()
st.caption("※ 가상 데이터 기반 시연 도구 · 실제 거래처 정보와 무관 · 롯데백화점 「롯데인들 with AI」 바이브코딩 출품작")
