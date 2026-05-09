from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import time
import json
import os
import re
import requests
from datetime import datetime

# Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# ⚙️ 설정값
# ==========================================
START_DATE = "2026.04.01."
MAX_ARTICLES = 50
MAX_PAGES = 10

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
FIREBASE_CREDENTIALS = os.environ.get("FIREBASE_CREDENTIALS")

# ==========================================
# 카테고리 분류 규칙
# ==========================================
CATEGORY_RULES = [
    ("공모전", ["공모전"]),
    ("신청글", [
        "수강", "신청", "비교과", "특강", "채용", "프로그램", "모집",
        "창업", "사업", "지원", "공고", "선발", "참가자", "참여자",
        "육성", "발굴", "장학", "인턴", "교육생"
    ]),
    ("이벤트", ["이벤트"]),
]
GUIDE_KEYWORDS = ["안내", "홍보", "행사"]

def classify(title, is_notice):
    if is_notice:
        return "공지글"
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if keyword in title:
                return category
    for keyword in GUIDE_KEYWORDS:
        if keyword in title:
            return "홍보_안내"
    return "기타"

def parse_date(date_str):
    """날짜 문자열 파싱.
    - '2026.05.07.' 형식 → 해당 날짜
    - '19:18' 형식 (시:분) → 오늘 날짜로 처리 (네이버 카페는 당일 글에 시간만 표시)
    """
    try:
        cleaned = date_str.strip().rstrip(".")
        # 시간 형식 (HH:MM)이면 = 오늘 글
        if re.match(r'^\d{1,2}:\d{2}$', cleaned):
            today = datetime.now().date()
            return datetime.combine(today, datetime.min.time())
        # 일반 날짜 형식
        return datetime.strptime(cleaned, "%Y.%m.%d")
    except:
        return None

def normalize_link(link):
    """링크에서 page 파라미터 제거"""
    return re.sub(r'[?&]page=\d+', '', link)

START_DATETIME = parse_date(START_DATE)

# ==========================================
# Firebase 초기화
# ==========================================
db = None
if FIREBASE_CREDENTIALS:
    try:
        cred_dict = json.loads(FIREBASE_CREDENTIALS)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("🔥 Firebase 연결 성공")
    except Exception as e:
        print(f"❌ Firebase 연결 실패: {e}")
else:
    print("⚠️ FIREBASE_CREDENTIALS가 설정되지 않음")

# ==========================================
# 디스코드 알림 함수
# ==========================================
def send_discord_notification(article, category):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ DISCORD_WEBHOOK_URL이 설정되지 않아 알림 생략")
        return

    message = (
        f"🆕 **새 글 알림** [{category}]\n"
        f"📌 **{article['title']}**\n"
        f"👤 작성자: {article['author']}　📅 날짜: {article['date']}\n"
        f"🔗 {article['link']}"
    )

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
        print(f"📬 디스코드 알림 전송: {article['title']}")
    except Exception as e:
        print(f"❌ 디스코드 전송 실패: {e}")

# ==========================================
# Firestore에서 이전 글 목록 불러오기
# ==========================================
previous_links = set()
is_first_run = True

CATCH_UP_FROM = datetime(2026, 5, 6).date()  # 이 날짜 이후 글은 무조건 새 글로 취급
CATCH_UP_MODE = False  # ← 복구 완료 후 False로 바꾸세요!

if db:
    try:
        articles_ref = db.collection("articles").stream()
        for doc in articles_ref:
            data = doc.to_dict()
            for article in data.get("items", []):
                article_date_stored = parse_date(article.get("date", ""))
                # ⭐ 복구 모드: CATCH_UP_FROM 이후 글은 previous_links에 넣지 않음
                # → 크롤링 시 "새 글"로 인식되어 알림 발송
                if CATCH_UP_MODE and article_date_stored and article_date_stored.date() >= CATCH_UP_FROM:
                    continue
                previous_links.add(normalize_link(article["link"]))
        if previous_links:
            is_first_run = False
            print(f"📂 Firestore에서 이전 글 {len(previous_links)}개 로드 완료")
            if CATCH_UP_MODE:
                print(f"🔄 복구 모드: {CATCH_UP_FROM} 이후 글은 새 글로 처리")
        else:
            print("📂 Firestore에 이전 데이터 없음 (최초 실행)")
    except Exception as e:
        print(f"⚠️ Firestore 로드 실패: {e}")

# ==========================================
# 1. 크롬 브라우저 세팅 (headless 모드)
# ==========================================
options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

# ==========================================
# 2. 카페 게시판 접속 (페이지 순회)
# ==========================================
TARGET_CLUB_ID = "22694512"
TARGET_MENU_ID = "111"

categorized = {
    "공지글": [],
    "공모전": [],
    "신청글": [],
    "이벤트": [],
    "홍보_안내": [],
    "기타": []
}

new_articles = []
total_collected = 0
should_stop = False

for page in range(1, MAX_PAGES + 1):
    if should_stop or total_collected >= MAX_ARTICLES:
        break

    url = (f"https://cafe.naver.com/ArticleList.nhn?"
           f"search.clubid={TARGET_CLUB_ID}&search.menuid={TARGET_MENU_ID}"
           f"&search.boardtype=L&search.page={page}")

    driver.get(url)
    print(f"📄 {page}페이지 접속 중...")
    time.sleep(3)

    articles = driver.find_elements(By.CSS_SELECTOR, "a.article")

    if not articles:
        print(f"⚠️ {page}페이지에서 글을 찾지 못함, 중단")
        break

    for article in articles:
        if total_collected >= MAX_ARTICLES:
            should_stop = True
            break

        title = article.text.strip()
        link = article.get_attribute("href")

        row = article.find_element(By.XPATH, "./ancestor::tr")

        try:
            author = row.find_element(By.CSS_SELECTOR, "span.nickname").text.strip()
        except:
            author = "알 수 없음"

        try:
            date = row.find_element(By.CSS_SELECTOR, "td.type_date").text.strip()
        except:
            date = "알 수 없음"

        try:
            row.find_element(By.CSS_SELECTOR, "em.board-tag")
            is_notice = True
        except:
            is_notice = False

        article_date = parse_date(date)
        if not is_notice and article_date and article_date < START_DATETIME:
            print(f"⏹️ 기준일({START_DATE})보다 오래된 글 발견, 수집 종료")
            should_stop = True
            break

        category = classify(title, is_notice)

        article_data = {
            "title": title,
            "author": author,
            "date": date,
            "link": link
        }

        categorized[category].append(article_data)
        total_collected += 1

        # ⭐ 새 글 감지 (페이지 파라미터 제거 + 최근 2일 이내)
        if normalize_link(link) not in previous_links:
            today = datetime.now().date()
            if CATCH_UP_MODE and article_date and article_date.date() >= CATCH_UP_FROM:
                # 복구 모드: 지정한 날짜 이후 누락된 글 알림
                new_articles.append((article_data, category))
                print(f"🔄 [복구 모드] 알림 추가: {title} ({date})")
            elif not CATCH_UP_MODE and article_date and (today - article_date.date()).days <= 1:
                # 정상 모드: 오늘/어제 글만 알림
                new_articles.append((article_data, category))
            else:
                print(f"⏭️ 새 링크지만 오래된 글이라 알림 제외: {title} ({date})")

driver.quit()
print(f"\n✅ 브라우저 종료 완료 (총 {total_collected}개 수집)")

# ==========================================
# 3. 새 글 알림 전송
# ==========================================
if is_first_run:
    print(f"\n🔔 최초 실행이므로 알림은 보내지 않습니다 ({len(new_articles)}개 글 저장만)")
elif new_articles:
    print(f"\n🆕 새 글 {len(new_articles)}개 발견! 디스코드 알림 전송 중...")
    for article_data, category in new_articles:
        send_discord_notification(article_data, category)
        time.sleep(0.5)
else:
    print(f"\n✨ 새 글 없음")

# ==========================================
# 4. Firestore에 저장
# ==========================================
if db:
    try:
        # articles 컬렉션 - 카테고리별 문서로 저장
        for category, items in categorized.items():
            db.collection("articles").document(category).set({
                "items": items,
                "count": len(items),
                "updated_at": firestore.SERVER_TIMESTAMP
            })

        # metadata 컬렉션 - 전체 상태 저장
        db.collection("metadata").document("status").set({
            "updated_at": firestore.SERVER_TIMESTAMP,
            "start_date": START_DATE,
            "total": total_collected
        })

        print(f"\n🔥 Firestore 저장 완료")
    except Exception as e:
        print(f"❌ Firestore 저장 실패: {e}")

# 콘솔에 분류 결과 출력
print(f"\n📊 카테고리별 분류 결과:")
for category, items in categorized.items():
    print(f"  - {category}: {len(items)}개")

# ==========================================
# 5. (선택) 백업용 JSON 파일도 저장
# ==========================================
output = {
    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "start_date": START_DATE,
    "total": total_collected,
    "categories": categorized
}

with open("articles.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n✅ articles.json 백업 저장 완료")