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
MAX_POSTS_PER_CATEGORY = 20

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
FIREBASE_CREDENTIALS = os.environ.get("FIREBASE_CREDENTIALS")

# ⚠️ 임시: 네이버 카페 → 학교 사이트 전환 후 첫 실행
# Firestore에 기존 네이버 카페 링크가 남아있어 알림 폭탄 방지용
# 한 번 실행 후 False로 바꾸세요!
FORCE_FIRST_RUN = True

# ==========================================
# 크롤링할 카테고리 목록 (이름: slug)
# ==========================================
CATEGORIES = {
    "일반공지":    "notice",
    "학사공지":    "bachelor",
    "장학공지":    "scholarship",
    "취창업공지":  "jobs-info-board",
    "감염병공지":  "covid",
    "CK_On_Show":  "ckonshow",
    "언론이본청강": "press",
    "입찰정보":    "bidding",
    "채용정보":    "hire",
    "개인정보공시": "privacy",
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
    print("⚠️ FORCE_FIRST_RUN 모드: 알림 없이 저장만 합니다 (전환 후 첫 실행)")
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
# 카테고리별 글 수집
# ==========================================
categorized = {name: [] for name in CATEGORIES.keys()}
new_articles = []

for category_name, slug in CATEGORIES.items():
    print(f"\n📂 [{category_name}] 수집 중...")

    try:
        res = requests.get(
            f"{BASE_URL}/posts",
            params={
                "per_page": MAX_POSTS_PER_CATEGORY,
                "category_slug": slug,
                "_fields": "id,title,date,link",
                "orderby": "date",
                "order": "desc"
            },
            timeout=10
        )

        if res.status_code != 200:
            print(f"  ❌ API 오류: {res.status_code}")
            continue

        posts = res.json()
        print(f"  ✅ {len(posts)}개 수집")

        for post in posts:
            title = re.sub(r'<[^>]+>', '', post["title"]["rendered"]).strip()
            link = post["link"]
            date = post["date"][:10]

            article_data = {
                "title": title,
                "date": date,
                "link": link
            }

            categorized[category_name].append(article_data)

            # ⭐ 새 글 감지 (FORCE_FIRST_RUN이면 건너뜀)
            if not FORCE_FIRST_RUN and link not in previous_links:
                article_date = datetime.strptime(date, "%Y-%m-%d").date()
                today = datetime.now().date()
                if (today - article_date).days <= 1:
                    new_articles.append((article_data, category_name))
                else:
                    print(f"  ⏭️ 새 링크지만 오래된 글이라 알림 제외: {title}")

    except Exception as e:
        print(f"  ❌ [{category_name}] 수집 실패: {e}")

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
            "total": total_collected
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
    "total": total_collected,
    "categories": categorized
}

with open("articles.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n✅ articles.json 백업 저장 완료")
