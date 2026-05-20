import requests
import json
import os
import re
import time
from datetime import datetime

# Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# ⚙️ 설정값
# ==========================================
BASE_URL = "https://www.ck.ac.kr/wp-json/wp/v2"
CUTOFF_DATE = datetime(2026, 5, 1).date()  # 5월 이후 글만 수집
MAX_PAGES = 10                              # 최대 페이지 수 (안전장치)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
FIREBASE_CREDENTIALS = os.environ.get("FIREBASE_CREDENTIALS")

# ⚠️ 임시: 첫 실행 시 알림 없이 저장만
# 한 번 실행 후 False로 바꾸세요!
FORCE_FIRST_RUN = False

# ==========================================
# 크롤링할 카테고리 목록 (이름: 카테고리 ID)
# ==========================================
CATEGORIES = {
    "일반공지":    1,
    "학사공지":    32,
    "장학공지":    1340,
    "취창업공지":  43,
    "감염병공지":  1370,
    "CK_On_Show":  1079,
    "언론이본청강": 34,
    "입찰정보":    35,
    "채용정보":    36,
    "개인정보공시": 1342,
}

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
        f"📅 날짜: {article['date']}\n"
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

if FORCE_FIRST_RUN:
    print("⚠️ FORCE_FIRST_RUN 모드: 알림 없이 저장만 합니다")
elif db:
    try:
        articles_ref = db.collection("articles").stream()
        for doc in articles_ref:
            data = doc.to_dict()
            for article in data.get("items", []):
                previous_links.add(article["link"])
        if previous_links:
            is_first_run = False
            print(f"📂 Firestore에서 이전 글 {len(previous_links)}개 로드 완료")
        else:
            print("📂 Firestore에 이전 데이터 없음 (최초 실행)")
    except Exception as e:
        print(f"⚠️ Firestore 로드 실패: {e}")

# ==========================================
# 글 수집 함수
# ==========================================
def fetch_posts(category_id, category_name):
    """카테고리 ID로 5월 이후 글 수집 (페이지 순회)"""
    collected = []

    for page in range(1, MAX_PAGES + 1):
        try:
            res = requests.get(
                f"{BASE_URL}/posts",
                params={
                    "categories": category_id,  # ID로 필터링
                    "_fields": "id,title,date,link",
                    "per_page": 10,
                    "page": page,
                    "orderby": "date",
                    "order": "desc"
                },
                timeout=10
            )

            # 페이지 초과 시 종료
            if res.status_code == 400:
                print(f"  ✅ {page-1}페이지까지 수집 완료")
                break

            if res.status_code != 200:
                print(f"  ❌ 페이지 {page} API 오류: {res.status_code}")
                break

            posts = res.json()

            if not posts:
                print(f"  ✅ {page-1}페이지까지 수집 완료 (글 없음)")
                break

            stop = False
            for post in posts:
                title = re.sub(r'<[^>]+>', '', post["title"]["rendered"]).strip()
                link = post["link"]
                date = post["date"][:10]
                article_date = datetime.strptime(date, "%Y-%m-%d").date()

                # 5월 이전이면 수집 중단
                if article_date < CUTOFF_DATE:
                    print(f"  ⏹️ {CUTOFF_DATE} 이전 글 발견, 수집 종료")
                    stop = True
                    break

                collected.append({
                    "title": title,
                    "date": date,
                    "link": link
                })

            if stop:
                break

            print(f"  📄 {page}페이지 수집 완료 ({len(posts)}개)")

        except Exception as e:
            print(f"  ❌ {page}페이지 수집 실패: {e}")
            break

    return collected

# ==========================================
# 카테고리별 글 수집
# ==========================================
categorized = {name: [] for name in CATEGORIES.keys()}
new_articles = []

for category_name, category_id in CATEGORIES.items():
    print(f"\n📂 [{category_name}] 수집 중...")
    posts = fetch_posts(category_id, category_name)
    categorized[category_name] = posts
    print(f"  → 총 {len(posts)}개 수집")

    # ⭐ 새 글 감지 (FORCE_FIRST_RUN이면 건너뜀)
    if not FORCE_FIRST_RUN:
        for article_data in posts:
            if article_data["link"] not in previous_links:
                article_date = datetime.strptime(article_data["date"], "%Y-%m-%d").date()
                today = datetime.now().date()
                if (today - article_date).days <= 1:
                    new_articles.append((article_data, category_name))
                else:
                    print(f"  ⏭️ 새 링크지만 오래된 글이라 알림 제외: {article_data['title']}")

total_collected = sum(len(v) for v in categorized.values())
print(f"\n✅ 전체 수집 완료 (총 {total_collected}개)")

# ==========================================
# 새 글 알림 전송
# ==========================================
if FORCE_FIRST_RUN or is_first_run:
    print(f"\n🔔 최초 실행이므로 알림은 보내지 않습니다 (데이터 저장만)")
elif new_articles:
    print(f"\n🆕 새 글 {len(new_articles)}개 발견! 디스코드 알림 전송 중...")
    for article_data, category in new_articles:
        send_discord_notification(article_data, category)
        time.sleep(0.5)
else:
    print(f"\n✨ 새 글 없음")

# ==========================================
# Firestore에 저장
# ==========================================
if db:
    try:
        for category_name, items in categorized.items():
            db.collection("articles").document(category_name).set({
                "items": items,
                "count": len(items),
                "updated_at": firestore.SERVER_TIMESTAMP
            })

        db.collection("metadata").document("status").set({
            "updated_at": firestore.SERVER_TIMESTAMP,
            "total": total_collected,
            "cutoff_date": str(CUTOFF_DATE)
        })

        print(f"\n🔥 Firestore 저장 완료")
    except Exception as e:
        print(f"❌ Firestore 저장 실패: {e}")

# 결과 출력
print(f"\n📊 카테고리별 수집 결과:")
for category_name, items in categorized.items():
    print(f"  - {category_name}: {len(items)}개")

# ==========================================
# 백업용 JSON 저장
# ==========================================
output = {
    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "cutoff_date": str(CUTOFF_DATE),
    "total": total_collected,
    "categories": categorized
}

with open("articles.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n✅ articles.json 백업 저장 완료")
