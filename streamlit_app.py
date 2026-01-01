"""
한국 FHR 호텔 가격 대시보드 - 개선 버전 (그리드 레이아웃)
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from storage import HotelStorage
from datetime import datetime

st.set_page_config(
    page_title="FHR 호텔 최저가",
    page_icon="🏨",
    layout="wide"
)

# CSS - 그리드 레이아웃
st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.8/dist/web/static/pretendard.css");
html, body, [class*="css"] { 
    font-family: 'Pretendard', sans-serif;
    font-size: 16px;
}

/* 제목 */
h1 {
    background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 900;
    font-size: 2.5rem !important;
    margin-bottom: 20px !important;
}

h2 {
    font-size: 1.6rem !important;
    font-weight: 700;
    color: #fff;
    margin-top: 20px !important;
}

h3 {
    font-size: 1.3rem !important;
    font-weight: 600;
}

/* 호텔 카드 */
.hotel-card {
    background: linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%);
    border: 2px solid rgba(102, 126, 234, 0.3);
    border-radius: 16px;
    padding: 20px;
    transition: all 0.3s ease;
    height: 100%;
}

.hotel-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 28px rgba(102, 126, 234, 0.3);
    border-color: rgba(102, 126, 234, 0.6);
}

.hotel-name {
    font-size: 1.2rem;
    font-weight: 700;
    color: #fff;
    margin-bottom: 8px;
}

.price-big {
    font-size: 2rem;
    font-weight: 900;
    margin: 12px 0;
}

.price-down { color: #ff6b6b; }
.price-up { color: #51cf66; }
.price-same { color: #ffd43b; }
.price-lowest { color: #ff4757; }

.info-badge {
    display: inline-block;
    background: rgba(255, 255, 255, 0.1);
    padding: 4px 10px;
    border-radius: 12px;
    margin-right: 6px;
    font-size: 0.85rem;
    margin-top: 6px;
}

.lowest-badge {
    background: linear-gradient(90deg, #ff6b6b 0%, #ff4757 100%);
    color: white;
    padding: 6px 12px;
    border-radius: 12px;
    font-weight: 700;
    font-size: 0.9rem;
    display: inline-block;
    margin-top: 8px;
}

/* 메트릭 카드 */
[data-testid="stMetricValue"] {
    font-size: 2rem !important;
    font-weight: 900 !important;
}

[data-testid="stMetricLabel"] {
    font-size: 1rem !important;
    font-weight: 600 !important;
}

/* 탭 */
.stTabs [data-baseweb="tab-list"] {
    gap: 16px;
}

.stTabs [data-baseweb="tab"] {
    font-size: 1.1rem;
    font-weight: 600;
    padding: 10px 20px;
}

/* 사이드바 */
section[data-testid="stSidebar"] > div {
    padding-top: 2rem;
}
</style>
""", unsafe_allow_html=True)

# 데이터 로드
@st.cache_data(ttl=3600)
def load_data():
    storage = HotelStorage(base_dir="data")
    history = storage.load_history()
    logs = storage.load_logs()
    return history, logs

# 메인
st.title("🏨 FHR 호텔 최저가 트래커")

history, logs = load_data()

if not history:
    st.warning("⚠️ 데이터가 없습니다.")
    st.stop()

# 전체 통계 (컴팩트하게)
st.markdown("### 📊 전체 현황")
col1, col2, col3, col4 = st.columns(4)

hotels_df = pd.DataFrame([
    {
        "code": code,
        "name": info["name"],
        "price": info["price"],
        "earliest": info.get("earliest", ""),
        "credit": info.get("credit", 100),
        "all_time_low": info.get("all_time_low", info["price"]),
        "is_lowest": info["price"] == info.get("all_time_low", info["price"])
    }
    for code, info in history.items()
])

with col1:
    st.metric("📍 총 호텔", f"{len(hotels_df)}개")

with col2:
    avg_price = hotels_df["price"].mean()
    st.metric("💵 평균", f"${avg_price:.0f}")

with col3:
    lowest_count = sum(hotels_df["is_lowest"])
    st.metric("🔥 최저", f"{lowest_count}개")

with col4:
    min_price = hotels_df["price"].min()
    st.metric("💎 최소", f"${min_price}")

st.markdown("---")

# 사이드바 필터
st.sidebar.header("🔍 필터")

# 도시 필터
city_map = {
    "전체": "",
    "🏙️ 서울": "Seoul",
    "🌊 부산": "Busan",
    "🏝️ 제주": "Jeju"
}
selected_city = st.sidebar.selectbox("도시 선택", list(city_map.keys()))

# 가격대 필터
max_price = int(hotels_df["price"].max())
price_range = st.sidebar.slider(
    "가격대 ($)",
    min_value=0,
    max_value=max_price + 100,
    value=(0, max_price + 100),
    step=50
)

# 정렬
sort_options = {
    "💰 가격 낮은순": ("price", True),
    "💸 가격 높은순": ("price", False),
    "🔥 역대최저가만": ("is_lowest", False),
    "💳 크레딧 높은순": ("credit", False),
    "🔤 이름순": ("name", True)
}
selected_sort = st.sidebar.selectbox("정렬", list(sort_options.keys()))

# 역대최저가만 보기
show_lowest_only = st.sidebar.checkbox("🔥 역대최저가만 보기")

# 탭
tab1, tab2 = st.tabs(["💰 호텔 목록", "📈 가격 추이"])

with tab1:
    # 필터링
    filtered_df = hotels_df.copy()
    
    if selected_city != "전체":
        city_keyword = city_map[selected_city]
        filtered_df = filtered_df[filtered_df["name"].str.contains(city_keyword, case=False)]
    
    filtered_df = filtered_df[
        (filtered_df["price"] >= price_range[0]) & 
        (filtered_df["price"] <= price_range[1])
    ]
    
    if show_lowest_only:
        filtered_df = filtered_df[filtered_df["is_lowest"]]
    
    # 정렬
    sort_col, sort_asc = sort_options[selected_sort]
    if sort_col == "is_lowest":
        filtered_df = filtered_df[filtered_df["is_lowest"]].sort_values("price")
    else:
        filtered_df = filtered_df.sort_values(sort_col, ascending=sort_asc)
    
    st.subheader(f"총 {len(filtered_df)}개 호텔")
    
    if len(filtered_df) == 0:
        st.info("🔍 필터 조건에 맞는 호텔이 없습니다.")
    else:
        # 3열 그리드로 호텔 카드 표시
        hotels_list = filtered_df.to_dict('records')
        
        # 3개씩 묶어서 row 생성
        for i in range(0, len(hotels_list), 3):
            cols = st.columns(3)
            
            for j, col in enumerate(cols):
                if i + j < len(hotels_list):
                    hotel = hotels_list[i + j]
                    is_lowest = hotel["is_lowest"]
                    price_class = "price-lowest" if is_lowest else "price-same"
                    icon = "🔥" if is_lowest else "🏨"
                    
                    with col:
                        st.markdown(f"""
                        <div class="hotel-card">
                            <div class="hotel-name">{icon} {hotel['name']}</div>
                            <div class="price-big {price_class}">${hotel['price']}</div>
                            <div>
                                <span class="info-badge">📅 {hotel['earliest'] if hotel['earliest'] else '날짜 미정'}</span>
                                <span class="info-badge">💳 ${hotel['credit']}</span>
                            </div>
                            {f'<div class="lowest-badge">✨ 역대 최저가!</div>' if is_lowest else ''}
                        </div>
                        """, unsafe_allow_html=True)

with tab2:
    st.subheader("📈 가격 추이 분석")
    
    if not logs:
        st.info("📊 아직 이력 데이터가 없습니다. 내일부터 차트가 표시됩니다.")
    else:
        col1, col2 = st.columns([2, 1])
        
        # 호텔 선택
        with col1:
            hotel_names = sorted([info["name"] for info in history.values()])
            selected_hotel = st.selectbox(
                "🏨 호텔 선택", 
                hotel_names, 
                key="price_chart_hotel"
            )
        
        # 기간 선택
        with col2:
            period_options = {
                "최근 7일": 7,
                "최근 14일": 14,
                "최근 30일": 30,
                "최근 90일": 90,
                "최근 6개월": 180,
                "최근 1년": 365,
                "📊 전체 기간": None
            }
            selected_period = st.selectbox(
                "기간", 
                list(period_options.keys()), 
                index=2,
                key="price_chart_period"
            )
            period_days = period_options[selected_period]
        
        # 해당 호텔의 code 찾기
        hotel_code = None
        for code, info in history.items():
            if info["name"] == selected_hotel:
                hotel_code = code
                break
        
        if hotel_code:
            storage = HotelStorage(base_dir="data")
            all_history = storage.get_price_history_for_hotel(hotel_code, days=None)
            
            if period_days is not None and len(all_history) > period_days:
                price_history = all_history[-period_days:]
            else:
                price_history = all_history
            
            if len(price_history) < 2:
                st.info("📊 차트를 표시하려면 최소 2일 이상의 데이터가 필요합니다.")
            else:
                # 차트 데이터
                dates = [h["date"] for h in price_history]
                prices = [h["price"] for h in price_history]
                
                # Plotly 차트
                fig = go.Figure()
                
                # 메인 라인
                fig.add_trace(go.Scatter(
                    x=dates,
                    y=prices,
                    mode='lines+markers',
                    name='가격',
                    line=dict(color='#667eea', width=4),
                    marker=dict(size=10, color='#764ba2'),
                    hovertemplate="<b>%{x}</b><br>가격: $%{y}<extra></extra>",
                    fill='tozeroy',
                    fillcolor='rgba(102, 126, 234, 0.1)'
                ))
                
                # 역대 최저가 라인
                all_time_low = history[hotel_code]["all_time_low"]
                fig.add_hline(
                    y=all_time_low,
                    line_dash="dash",
                    line_color="#ff4757",
                    line_width=3,
                    annotation_text=f"🔥 역대 최저 ${all_time_low}",
                    annotation_position="right",
                    annotation_font_size=14,
                    annotation_font_color="#ff4757"
                )
                
                # 평균 라인
                avg_price = sum(prices) / len(prices)
                fig.add_hline(
                    y=avg_price,
                    line_dash="dot",
                    line_color="#ffd43b",
                    line_width=2,
                    annotation_text=f"📊 평균 ${avg_price:.0f}",
                    annotation_position="left",
                    annotation_font_size=12,
                    annotation_font_color="#ffd43b"
                )
                
                period_text = selected_period
                fig.update_layout(
                    title={
                        'text': f"{selected_hotel} - {period_text}",
                        'font': {'size': 22, 'color': '#fff', 'family': 'Pretendard'}
                    },
                    xaxis_title="날짜",
                    yaxis_title="가격 ($)",
                    height=500,
                    template="plotly_dark",
                    hovermode="x unified",
                    plot_bgcolor='rgba(0,0,0,0)',
                    paper_bgcolor='rgba(0,0,0,0)',
                )
                
                st.plotly_chart(fig, use_container_width=True)
                
                # 통계 카드
                st.markdown("### 📊 기간 통계")
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric("현재", f"${prices[-1]}")
                
                with col2:
                    st.metric("평균", f"${avg_price:.0f}")
                
                with col3:
                    st.metric("최저", f"${min(prices)}")
                
                with col4:
                    st.metric("최고", f"${max(prices)}")
                
                # 가격 변동
                if len(prices) > 1:
                    price_change = prices[-1] - prices[0]
                    change_pct = (price_change / prices[0] * 100) if prices[0] != 0 else 0
                    
                    change_color = "🔻" if price_change < 0 else "🔺" if price_change > 0 else "➡️"
                    change_text = f"{change_color} 기간 내 변동: ${price_change:+.0f} ({change_pct:+.1f}%)"
                    
                    if price_change < 0:
                        st.success(change_text)
                    elif price_change > 0:
                        st.error(change_text)
                    else:
                        st.info(change_text)

# 푸터
st.markdown("---")
col1, col2 = st.columns([3, 1])
with col1:
    st.caption(f"🕐 마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
with col2:
    st.caption("📊 데이터 출처: MaxFHR, AMEX FHR")
