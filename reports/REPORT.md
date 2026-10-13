# Lab 25B — Report

**Họ tên / nhóm:** Ngô Anh Khoa — MSV: 2A202602965  **Commit:** 10029bc  **Điểm tự động (`make grade`):** 84 / 88 (+ 4đ SLO hidden dataset = 88/88)

> Mọi nhận định phải có số. Dán số từ `reports/gameday/*.json`, `reports/workflows.json`, `reports/slo.json`.

## 1. Kết quả Game Day (naive vs của bạn)

| Băng | Client | availability | p95 (s) | amplification | retry_ratio | cost/turn | recovery (s) | PASS? |
|---|---|---:|---:|---:|---:|---:|---:|:---:|
| G1 | naive | 0.7194 | 7.157 | 7.738 | 6.738 | 2.163 | 4.0 | FAIL |
| G1 | yours | 1.0000 | 4.776 | 1.062 | 0.002 | 2.101 | 0.0 | PASS |
| G2 | naive | 0.4560 | 15.034 | 9.904 | 8.904 | 4.627 | 155.0 | FAIL |
| G2 | yours | 1.0000 | 11.911 | 1.059 | 0.008 | 2.161 | 0.0 | PASS |
| G3 | naive | 1.0000 | 4.768 | 1.164 | 0.164 | 2.328 | 0.0 | FAIL |
| G3 | yours | 1.0000 | 4.738 | 1.058 | 0.034 | 2.141 | 0.0 | PASS |
| G4 | naive | 1.0000 | 13.764 | 1.004 | 0.004 | 2.008 | 0.0 | FAIL |
| G4 | yours | 1.0000 | 5.562 | 1.015 | 0.002 | 2.072 | 0.0 | PASS |
| G5 | naive | 0.3699 | 5.359 | 17.030 | 16.030 | 2.124 | None | FAIL |
| G5 | yours | 0.8179 | 5.477 | 1.003 | 0.002 | 2.504 | None | PASS |
| G6 | naive | 0.5170 | 4.836 | 12.344 | 11.344 | 2.009 | 0.0 | FAIL |
| G6 | yours | 1.0000 | 5.961 | 1.055 | 0.018 | 2.196 | 0.0 | PASS |

## 2. Cấu hình client và lý do

| Tham số | Giá trị | Vì sao (dựa trên băng nào, số liệu nào) |
|---|---:|---|
| attempt_timeout_s | 8.0 | Ở băng G6 (alpha sập hoàn toàn), nếu timeout dài 30s thì client bị kẹt quá lâu và hết deadline của user (availability tụt còn 94.88%, 108 lỗi deadline). 8s vừa đủ cho 1 attempt bình thường (median 1.2s-1.8s) và còn đủ thời gian failover sang provider khác trước deadline 30s. |
| max_attempts_per_provider | 2 | Ở băng G2 và G3, chỉ cho phép thử lại tối đa 1 lần trên cùng provider (tổng 2 attempts) trước khi failover, giúp giữ retry_ratio ở mức rất thấp (0.008 trên G2 và 0.034 trên G3 so với 8.904 và 0.164 của naive). |
| backoff_base_s / cap_s | 0.25 / 4.0 | Base 0.25s tránh retry ngay lập tức tạo thundering herd; cap 4.0s kết hợp full jitter giúp rải đều tải khi provider hồi phục, không bị dồn cục ở cùng một tích tắc. |
| retry_budget_ratio | 0.1 | Ở băng G3 (brown-out 30% lỗi 500), khống chế chặt chẽ tổng lượng retry không vượt quá 10% traffic gốc, đảm bảo vượt qua điều kiện kiểm tra retry_ratio <= 0.10 (thực tế đạt 0.034). |
| breaker.window_size / min_calls | 20 / 10 | Đủ mẫu (10 calls) để tránh ngắt nhầm khi có vài lỗi ngẫu nhiên ban đầu, nhưng cửa sổ 20 đủ nhạy để ngắt mạch nhanh chóng khi sự cố xảy ra (G2, G4). |
| breaker.failure_rate_threshold | 0.5 | Khi tỷ lệ lỗi vượt quá 50% trong 20 calls gần nhất (như G2 mất 75% GPU dẫn đến 529), breaker chuyển sang OPEN ngay lập tức để chuyển hướng lưu lượng sang alpha-eu và beta. |
| breaker.slow_call_s | 5.0 | Ở băng G4 (alpha bị chậm thêm 5s mà không trả lỗi), ngưỡng 5.0s bắt trúng sự suy thoái hiệu năng, ngắt mạch sớm giúp p95 đạt 5.562s (naive không bắt được nên p95 vọt lên 13.764s). |
| breaker.slow_rate_threshold | 0.6 | Tỷ lệ cuộc gọi chậm >= 60% kích hoạt ngắt mạch, bảo vệ p95 của toàn hệ thống dưới 10s trong G4. |
| breaker.open_duration_s | 10.0 | Sau khi ngắt 10s, breaker thử chuyển sang HALF_OPEN để thăm dò sức khỏe provider; nếu provider đã hồi phục thì đóng mạch lại, không bỏ rơi provider chính quá lâu (recovery_s = 0s). |
| breaker.half_open_max_probes | 2 | Chỉ cho phép tối đa 2 request đang bay (in-flight) thăm dò provider đang hồi phục; ngăn chặn thundering herd làm sập lại provider vừa mới tỉnh dậy. |

Sơ đồ quyết định của `on_error`:

```
                    ┌───────────────────────────┐
                    │      on_error(attempt)    │
                    └─────────────┬─────────────┘
                                  │ classify(err)
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
  CLIENT_ERROR            QUOTA_EXHAUSTED /           OVERLOADED /
  (400, 401, 422)           RATE_LIMITED              SERVER_ERROR /
         │                  (429 quota / 429 limit)    TIMEOUT
         │                        │                        │
  release permit           release permit            on_failure permit
         │                        │                        │
  GiveUp("client_error")   cooldown provider         tries < max_attempts
  (KHÔNG retry,            (quota: 300s,              AND budget token >= 1
   KHÔNG failover)          rate-limit: retry-after)  AND fits deadline?
                                  │                        │
                           Failover now             ┌──────┴──────┐
                           (_route next provider)   YES           NO
                                                    │             │
                                              Retry SAME      Failover now
                                              (full jitter)   (_route next)
```

## 3. Ba thí nghiệm (mỗi cái 3–5 câu + số)

1. **Retry budget là lớp phòng thủ cuối:**  
   Khi giả lập breaker bị vô hiệu hóa cấu hình sai (`min_calls: 1000000000`, `max_attempts: 5`), nếu **giữ retry budget**, hệ thống vẫn đứng vững: G2 đạt amplification = 1.382, retry_ratio = 0.058, recovery = 0.0s, availability = 99.96%; G3 đạt amplification = 1.135, retry_ratio = 0.067, availability = 100%.  
   Tuy nhiên khi **tắt retry budget**, thảm họa xảy ra: ở G2, retry_ratio vọt lên 0.491 (tăng hơn 8 lần), recovery_s bị kéo dài lên 30.0s, availability sụt xuống 96.49%; ở G3, retry_ratio tăng lên 0.162 (vượt trần 0.10 của rubric).  
   *Kết luận:* Breaker bảo vệ provider khi provider đã có dấu hiệu hỏng, nhưng nếu breaker chưa kịp mở hoặc bị lỗi cấu hình, retry budget là van an toàn tuyệt đối chặn đứng retry storm bằng cách giới hạn cứng số lượng retry trên toàn hệ thống. Cần cả hai để tạo kiến trúc phòng thủ chiều sâu (defense-in-depth).

2. **`attempt_timeout_s: 30`:**  
   Khi nâng attempt timeout lên 30s, ở băng G6 (alpha sập 503, alpha-eu treo hoàn toàn), availability sụt giảm từ 100% xuống **94.88%** (FAIL vì yêu cầu >= 95%), xuất hiện **108 lượt GiveUp do `deadline`** và 14 lượt `no_provider_available`. Đáng chú ý, `latency_p95_s` ở G6 lại giảm từ 5.961s xuống **5.425s** — đây là hiện tượng **Survivorship bias** kinh điển trong SRE: các request bị treo 30s đều chết do deadline của user (30s) và không được tính vào p95 (p95 chỉ tính trên lượt thành công).  
   Ngược lại, ở G4, availability vẫn giữ 100.0% và p95 đạt 5.683s (hầu như không đổi) vì cơ chế slow-call của circuit breaker (5.0s) đã chủ động ngắt mạch từ sớm trước khi timeout 30s kịp can thiệp.  
   *Kết luận:* Timeout "đúng" không được dựa vào deadline tối đa của user mà phải tính dựa trên p99 lúc bình thường cộng thêm một khoảng đệm an toàn nhỏ để kịp thời failover sang provider dự phòng trước khi user hết kiên nhẫn.

3. **Bỏ lọc tool calling khỏi `ladder()`:**  
   Khi bỏ lọc khả năng tool calling trong `ladder()` ở băng G6, hệ thống ghi nhận **256 cuộc gọi `incompatible_routes`** và availability tụt từ 100% xuống **89.27%**.  
   *Ngôn ngữ Product Manager:* "Trong đợt sự cố nhà cung cấp, hệ thống đã điều hướng sai 256 yêu cầu xử lý giao dịch hoàn tiền của khách hàng sang một mô hình ngôn ngữ mini giá rẻ (`gamma-mini`) vốn hoàn toàn không có khả năng thực hiện lệnh hệ thống. Hậu quả là 256 khách hàng nhận thông báo lỗi giao dịch thất bại, làm giảm 10.73% tỷ lệ phục vụ thành công và gây tổn hại nghiêm trọng đến trải nghiệm người dùng."

## 4. Workflow hoàn tiền

| Executor | completion | crashes | duplicate refunds | duplicate emails | lost refunds | overpaid | wasted LLM / crash |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive | 1.0 | 81 | 128 | 81 | 0 | 1,496,700 | 1.123 |
| yours | 1.0 | 66 | 0 | 0 | 0 | 0 | 0.303 |

**Trả lời:**  
- **Vì sao chỉ idempotency key là chưa đủ?** Nếu chỉ có idempotency key mà không có checkpoint, khi worker bị crash sau bước thanh toán, worker mới khởi động lại sẽ buộc phải thực thi lại toàn bộ các bước tính toán trước đó (classify, lookup) từ đầu, gây lãng phí lớn chi phí LLM và tài nguyên CPU (`wasted_llm_per_crash` = 1.123 ở naive).  
- **Vì sao chỉ checkpoint là chưa đủ?** Nếu chỉ có checkpoint mà không có idempotency key, khi worker gọi lệnh thanh toán ngân hàng (`issue_refund`), tiền đã trừ thành công nhưng mạng bị timeout khiến worker tưởng là lỗi và thử lại, ngân hàng sẽ thực hiện lệnh trừ tiền lần 2 dẫn đến hoàn tiền trùng lặp (naive bị 128 lần duplicate refund, thất thoát 1,496,700 cents).  
- **Điều gì xảy ra nếu ghi checkpoint TRƯỚC khi gọi payment?** Nếu ghi checkpoint hoàn tiền thành công vào store trước khi thực sự gọi API thanh toán, lỡ worker bị crash ngay tại khoảnh khắc giữa 2 dòng code đó, worker mới khi resume sẽ đọc checkpoint và đinh ninh rằng tiền đã được hoàn trả, dẫn đến việc bỏ qua bước thanh toán và **khách hàng bị mất tiền hoàn vĩnh viễn** (`lost_refunds > 0`).

## 5. Alerting & error budget

| Sự cố | Mong đợi | Naive alerter | Burn-rate alerter | Độ trễ phát hiện |
|---|---|---|---|---:|
| checkout_bug | page | page (1 min) | page (27 min) | 27 min |
| provider_outage | page | page (0 min) | page (4 min) | 4 min |
| deploy_blip | none | **page (false alarm)** | none | Không bị báo động |
| slow_leak | ticket | **none (missed)** | ticket (1337 min) | 1337 min |
| degraded_evening | page | page (214 min) | page (291 min) | 291 min |
| night_spike | none | **page (false alarm)** | none | Không bị báo động |
| small_bump | none | **page (false alarm)** | none | Không bị báo động |

**Error budget 30 ngày:**  
- Tổng số request: 25,901,755  
- Lỗi cho phép (0.5%): 129,508.8  
- Lỗi thực tế ghi nhận: 55,618  
- Availability đạt: 99.7853% (vượt mục tiêu SLO 99.5%)  
- Ngân sách lỗi đã tiêu: **42.95%**, còn lại: **57.05%**.  
- **Quyết định đóng băng release:** **KHÔNG**. Do budget remaining vẫn còn dương (57.05% > 0), đội ngũ kỹ thuật vẫn được phép tiếp tục deploy release mới bình thường.  
- **Vì sao `small_bump` (20 phút ở 12% lỗi) không nên đánh thức ai lúc 11 giờ trưa?** Sự cố này chỉ diễn ra trong 20 phút rồi tự chấm dứt, tổng ngân sách lỗi bị tiêu hao chỉ khoảng 0.3% error budget tháng. Ngưỡng cửa sổ dài của Burn-Rate Alerter (1 giờ và 6 giờ) giúp bộ lọc nhận ra sự cố này không đe dọa đến mục tiêu SLO 30 ngày, và cửa sổ ngắn (5 phút/30 phút) giúp cảnh báo tự reset ngay khi hết lỗi. Việc gọi page trực tiếp sẽ làm ngắt quãng công việc của kỹ sư (alert fatigue) cho một sự cố tự phục hồi không gây nguy hiểm.

## 6. Postmortem — Băng G2 với client naive (blameless)

*Dùng `make plot` (hình `reports/gameday/G2_overload_storm.png`) và CSV `reports/gameday/naive_G2_overload_storm_timeseries.csv`.*

**Tóm tắt (3 câu):**  
Vào lúc t=120s của kịch bản mô phỏng G2, cụm provider `alpha` bị suy giảm đột ngột 75% năng lực GPU trong 180 giây. Do client v1 cấu hình 3 tầng retry lồng nhau không có jitter, không có retry budget và không có circuit breaker, lượng request retry dồn dập đã biến sự cố hạ tầng thành một cơn bão retry storm và sập nguồn dạng metastable failure. Hệ thống mất hoàn toàn khả năng phục vụ trong suốt 335 giây, ngay cả khi provider đã khôi phục 100% năng lực từ t=300s, kéo dài thời gian phục hồi thêm 155s sau sự cố.

**Ảnh hưởng:**  
- Tổng số lượt người dùng thất bại: **1,285 lượt** (chỉ có 1,077/2,362 lượt thành công, availability rơi thảm hại xuống 45.60%).  
- Thời gian gián đoạn: Kéo dài **335 giây** (từ t=120s đến t=455s).  
- Tiêu tốn ngân sách lỗi: Sự cố làm phát sinh hơn 30,000 lỗi và timeout, tiêu sạch **hơn 180% error budget của cả tháng** nếu chỉ xét riêng sự cố này.

**Timeline (giây ảo — trích xuất từ `naive_G2_overload_storm_timeseries.csv`):**

| t | Sự kiện | Bằng chứng (số) |
|---:|---|---|
| 120 | alpha mất 75% năng lực GPU | `calls_ok` giảm từ 7 xuống 2, độ trễ bắt đầu tăng vọt, hàng đợi server bắt đầu ứ đọng. |
| 136 | Bắt đầu bùng phát Retry Storm | Các request đầu tiên hết hạn timeout 8s, client lập tức retry dồn dập: `attempts_alpha` nhảy vọt từ 15 lên **53 attempts/s**. |
| 143 | Đỉnh điểm bão retry (Peak storm) | `attempts_alpha` đạt đỉnh **254 attempts/s**, `calls_ok` tụt thẳng về **0 calls/s**, 100% lượt người dùng bị nghẽn. |
| 220 | Hàng đợi server tê liệt hoàn toàn | Client liên tục gửi ~150 - 180 attempts/s; `calls_ok` duy trì liên tục ở mức **0**, server trả lỗi 529 hàng loạt. |
| 300 | alpha hồi phục 100% GPU về mặt hạ tầng | Provider khôi phục năng lực xử lý ban đầu, nhưng client vẫn duy trì **115 attempts/s**, `calls_ok` vẫn bằng **0**. |
| 350 | Hiện tượng Metastable Failure kéo dài | Sau khi provider sửa xong 50s, `calls_ok` vẫn bằng **0** vì hàng đợi FIFO của server ngập tràn request rác (zombie requests) mà client đã bỏ cuộc từ lâu. |
| 455 | Hệ thống bắt đầu phục hồi dần | Hàng đợi server xả hết request cũ, `recovery_s = 155.0s`, availability tổng chỉ đạt 45.60%. |

**Trigger vs root cause:**  
- **Trigger (Ngòi nổ):** Cụm server `alpha` bị mất 75% GPU xử lý tại t=120s do sự cố phần cứng.  
- **Root cause (Nguyên nhân gốc rễ):** Thiết kế client v1 có **3 tầng retry chồng chéo** (App × Gateway × SDK) tạo ra hệ số khuếch đại lên tới 9.904 lần; thiếu cơ chế Full Jitter khiến các retry dồn vào cùng một thời điểm; thiếu Retry Budget cho phép retry không giới hạn; và thiếu Circuit Breaker để ngắt mạch cách ly node đang quá tải.  
- **Cơ chế Metastable failure (Vòng lặp tự duy trì):** Khi server bị chậm, client timeout ở giây thứ 8 và từ bỏ, nhưng server vẫn tiếp tục tốn tài nguyên GPU xử lý request đã chết đó. Đồng thời, client gửi request mới và retry request cũ. Khi alpha hồi phục tại t=300s, hàng đợi của nó vẫn chứa đầy các request rác mà client đã timeout từ trước, khiến tải thực tế luôn vượt xa năng lực của server, tự giam mình trong trạng thái sập ngay cả khi nguyên nhân ban đầu đã biến mất.

**Điều gì giúp / điều gì làm tệ hơn:**  
- *Làm tệ hơn:* Client retry liên tục với delay cố định; không failover sang `alpha-eu` hay `beta`; server vẫn tiếp tục xử lý request đã bị client timeout thay vì drop sớm.  
- *Giúp ích:* Việc thay thế bằng `ResilientClient` với Circuit Breaker (ngắt mạch sau 10 calls hỏng), Full Jitter backoff và Retry Budget (tối đa 10% traffic) đã triệt tiêu hoàn toàn retry storm, đạt availability 100.0% và recovery = 0s.

**Action items:**

| # | Hành động | Loại (prevent / detect / mitigate) | Owner | Cách kiểm chứng |
|---|---|---|---|---|
| 1 | Triển khai ResilientClient với Circuit Breaker và Retry Budget <= 10% trên toàn bộ gateway | Prevent | SRE Team | Chạy CI test mô phỏng G2: amplification <= 1.2 và recovery <= 10s. |
| 2 | Cấu hình deadline propagation (gửi header deadline/cancellation xuống server để drop zombie request) | Mitigate | Backend Core | Kiểm tra log server: hủy xử lý ngay khi client ngắt kết nối. |
| 3 | Triển khai Burn-Rate Alerting đa cửa sổ (page khi burn rate 14.4x trong 1h & 5m) | Detect | On-call Team | Bắn test alert trên môi trường staging: nhận cảnh báo trong vòng 5 phút khi có sự cố lớn. |

## 7. Một điểm yếu còn lại

**Điểm yếu:**  
Trong kịch bản cả 4 provider cùng bị suy giảm hiệu năng nhẹ hoặc khi `beta` phải gánh toàn bộ tải từ `alpha`, chi phí trên mỗi lượt thành công (`cost_per_good_turn`) tăng lên đáng kể (beta đắt hơn 40% so với alpha: cost 1.4 vs 1.0). Ngoài ra, nếu toàn bộ 4 provider cùng chậm đồng loạt nhưng chưa chạm ngưỡng lỗi 5xx, p95 của hệ thống vẫn có thể bị kéo dài sát ngưỡng 15s - 20s do client phải chờ lần lượt qua từng provider trong ladder.

**Cách khắc phục:**  
1. **Hedging / Speculative Execution có kiểm soát:** Khi một provider chính chưa phản hồi sau mốc p90 thời gian bình thường (ví dụ sau 2.0s), gửi một request thăm dò song song (speculative probe) sang provider dự phòng mà không hủy request ban đầu, request nào trả về trước thì dùng kết quả đó và hủy request còn lại.  
2. **Cost-aware Routing:** Đưa trọng số chi phí vào thuật toán fallback để phân bổ tỷ lệ phần trăm tải thông minh thay vì dồn toàn bộ sang provider đắt tiền nhất.

## 8. Cache (Part G)

Số từ `reports/cache/*.json` (`make cache`).

| Client | availability | faq_availability | cache_hit_ratio | stale_served | wrong_answers | outdated_answers | cost/turn | PASS? |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| resilient (không cache) | 0.8373 | 0.8424 | 0.000 | 0 | 0 | 0 | 1.386 | FAIL |
| cached (của bạn) | 0.9124 | 0.9645 | 0.652 | 157 | 0 | 3 | 0.915 | PASS |

| Tham số `cache:` | Giá trị | Vì sao (số liệu nào) |
|---|---:|---|
| similarity_threshold | 0.90 | Ở ngưỡng mặc định 0.80, có tới 157 câu hỏi bị trả lời sai (`wrong_answers = 157`) do các câu hỏi khác nhau bị nhận nhầm. Ngưỡng 0.90 triệt tiêu hoàn toàn lỗi sai (`wrong_answers = 0`) mà vẫn giữ `cache_hit_ratio` ở mức cao 65.2% (vượt xa yêu cầu >= 50%). |
| ttl_s | 60 | Khi bảng phí ship đổi âm thầm tại t=480 mà không có webhook báo trước, TTL = 60s giới hạn số câu trả lời lỗi thời chỉ còn 3 câu (`outdated_answers = 3`, đạt chuẩn <= 15). Nếu để TTL = 300s, số câu lỗi thời vọt lên 95 câu (FAIL). |
| max_stale_s | 300 | Đợt sập toàn bộ 4 provider kéo dài 120s (từ t=240s đến t=360s). Cửa sổ stale 300s cho phép cơ chế stale-if-error phục vụ 157 câu trả lời trong cache, giúp `faq_availability` đạt 96.45% (vượt chuẩn >= 93%). Nếu để max_stale_s = 0, faq_availability rơi xuống 87.33% (FAIL). |

**Thí nghiệm:**

1. **Ngưỡng:**  
   - Với `similarity_threshold = 0.80`: hit_ratio = 0.666, `wrong_answers` = 157 (lý do: `other_question = 157`), cost/turn = 0.965. Các câu hỏi có từ ngữ gần giống nhưng bản chất khác nhau (ví dụ: "thời gian hoàn tiền" vs "chính sách hoàn tiền") bị ánh xạ nhầm vào nhau.  
   - Với `similarity_threshold = 0.90`: hit_ratio = 0.652, `wrong_answers` = 0, cost/turn = 0.915 (tối ưu nhất).  
   - Với `similarity_threshold = 0.97`: hit_ratio giảm mạnh xuống 0.581, `wrong_answers` = 0, cost/turn tăng lên 0.964 do cache miss nhiều hơn, phải gọi LLM tốn kém hơn.

2. **TTL:**  
   - Khi chạy với `ttl_s` = 30: hit_ratio = 0.592, outdated_answers = 2.  
   - Khi chạy với `ttl_s` = 60: hit_ratio = 0.652, outdated_answers = 3.  
   - Khi chạy với `ttl_s` = 300: hit_ratio = 0.731, outdated_answers = 95 (vượt trần 15 của rubric!).  
   *Giải thích:* Do bảng phí ship tại t=480 bị thay đổi âm thầm mà không có webhook, hệ thống không nhận được sự kiện để chủ động gọi `invalidate()`. Vì vậy, TTL là cơ chế phòng thủ DUY NHẤT để loại bỏ các entry cũ sau một khoảng thời gian hữu hạn.

3. **Stale-if-error:**  
   - Với `max_stale_s: 0`: `faq_availability` sụt xuống **87.33%** (FAIL vì yêu cầu >= 93%), `stale_served` = 0. Trong suốt 120s mọi provider sập, mọi request miss cache đều bị GiveUp.  
   - Với `max_stale_s: 300`: `faq_availability` tăng vọt lên **96.45%** (PASS), phục vụ thành công **157 lượt stale answer**, giữ cho dịch vụ luôn khả dụng trong mắt khách hàng.

**Trả lời câu hỏi:**
- **Vì sao câu hỏi cá nhân ("Đơn #48213 của tôi đang ở đâu?") không được cache, kể cả khi cache theo từng user?**  
  1. *Nguy cơ rò rỉ dữ liệu cá nhân (Data Leak / PII):* Embedding similarity có thể ghép nhầm một câu hỏi cá nhân của người này vào câu hỏi của người khác nếu không kiểm soát tuyệt đối, dẫn đến hiển thị lộ thông tin giao dịch của người lạ.  
  2. *Tính biến động cao (High volatility):* Dữ liệu trạng thái đơn hàng thay đổi theo từng phút (đang đóng gói -> đang giao -> đã giao). Cache câu trả lời cá nhân sẽ trả lời sai trạng thái đơn hàng thực tế của khách, gây hiểu lầm nghiêm trọng.
- **Trong lúc mọi provider sập, dashboard vẫn xanh nhờ cache. Bạn sẽ alert dựa trên số nào để on-call vẫn biết có sự cố?**  
  Không được chỉ dựa vào metric HTTP Availability ở tầng ngoài cùng. On-call phải thiết lập alert dựa trên:  
  1. **Cache Origin Error Rate / Backend Failure Rate:** Tỷ lệ lỗi trả về từ các provider LLM bên dưới (khi tỷ lệ này tăng vọt, hệ thống đang phải dùng stale để chống cháy).  
  2. **Tỷ lệ phục vụ Stale (`stale_served_ratio`):** Số lượng câu trả lời stale trả về trên tổng số request. Khi `stale_served > 0` trong một khoảng thời gian nhất định, phải phát alert cảnh báo hạ tầng LLM đang gặp sự cố.
