# PR Review Agent — Kế hoạch triển khai MVP

## 1. Hiện trạng

Repository hiện chỉ có tài liệu thiết kế [`pr-review-agent-design.md`](./pr-review-agent-design.md),
chưa có source code và chưa được khởi tạo Git.

Thiết kế hiện tại khả thi cho MVP. Thứ tự triển khai nên ưu tiên một lát cắt end-to-end:

```text
fixture diff → review engine → database queue → GitHub webhook → Discord → dashboard
```

Thứ tự này giúp kiểm chứng sớm ba mục tiêu chính:

- Chất lượng nhận xét do mô hình tạo ra.
- Chi phí và thời gian xử lý mỗi Pull Request.
- Trải nghiệm nhận và đọc kết quả trên Discord.

## 2. Các điểm cần hoàn thiện trong thiết kế

### 2.1. Idempotency hai lớp

- Lưu `X-GitHub-Delivery` để nhận biết webhook bị gửi lại.
- Tạo unique constraint trên `(repository_id, pull_number, head_sha)` để cùng một commit
  không bị review hai lần.
- Webhook delivery và review run là hai khái niệm riêng: nhiều delivery có thể cùng trỏ tới
  một review run.

### 2.2. Queue có lease và retry

Ngoài `status`, `review_runs` cần các trường phục vụ worker:

- `attempt_count`
- `next_attempt_at`
- `locked_at`
- `locked_by`
- `lease_expires_at`
- `failure_code`
- `last_error`

Worker phải claim job bằng transaction nguyên tử. Job bị gián đoạn được worker khác thu hồi
sau khi lease hết hạn.

### 2.3. State machine

```text
queued → running → completed
                 ↘ failed
       → skipped
       → superseded
```

Khi Pull Request có commit mới, run cũ có thể tiếp tục xử lý nhưng không được gửi Discord
nếu `head_sha` không còn là HEAD. Run đó được đánh dấu `superseded` hoặc `skipped` với lý do
rõ ràng.

### 2.4. Data model cần bổ sung

Các trường nên có ngoài thiết kế ban đầu:

- `github_repository_id`: định danh ổn định khi repository đổi tên.
- `github_delivery_id`: chống webhook redelivery.
- `base_sha`, `trigger`, `model`, `prompt_version`, `config_snapshot`.
- `started_at`, `duration_ms`, `notified_at`.
- `confidence` trong `findings`.
- Cấu hình trigger events, `ignore_drafts` và `max_input_tokens`.
- Bảng `notification_deliveries` để theo dõi gửi và gửi lại Discord.

### 2.5. GitHub App lifecycle

Ngoài `pull_request`, hệ thống cần xử lý:

- `installation`
- `installation_repositories`

Các event này dùng để đồng bộ khi app được cài, gỡ, suspend hoặc thay đổi danh sách
repository được cấp quyền.

### 2.6. Diff lớn hoặc không đầy đủ

- GitHub Pull Request Files API có giới hạn số file trả về.
- Binary file hoặc patch quá lớn có thể không có nội dung để review.
- Hệ thống phải ghi lại số file/dòng bị bỏ qua và đánh dấu kết quả là partial review.
- Không được âm thầm xem partial review như một review đầy đủ.

### 2.7. Xác thực dashboard

Tiêu chí “chỉ repository admin được thay đổi cấu hình” cần một cơ chế xác thực rõ ràng.
Phương án phù hợp với MVP là GitHub OAuth kết hợp kiểm tra quyền trên từng repository.

Nếu dùng một tài khoản quản trị nội bộ để rút ngắn thời gian, tài liệu và giao diện phải ghi
rõ đây là chế độ single-operator và chưa đáp ứng tiêu chí phân quyền đầy đủ.

### 2.8. Secret, SSRF và prompt injection

- Discord webhook chỉ chấp nhận HTTPS và hostname Discord nằm trong allowlist.
- Discord webhook được mã hóa bằng master key nằm ngoài database.
- Frontend chỉ nhận trạng thái `configured`, không nhận ciphertext hoặc URL đầy đủ.
- PR title, body, comment và diff đều là dữ liệu không tin cậy.
- Nội dung từ PR không được thay đổi system instruction, kích hoạt tool hoặc yêu cầu thực thi
  code.
- Không log raw prompt, raw diff hoặc secret theo mặc định.

## 3. Kiến trúc triển khai

### 3.1. Thành phần chính

```text
GitHub App
    │
    ▼
Webhook API ──► SQLite queue/review_runs ──► Review Worker
                                              │
                                              ├──► GitHub API
                                              ├──► Model Provider
                                              └──► Discord Webhook

Dashboard ──► Application services ──► Database
```

### 3.2. Nguyên tắc phân lớp

- API route chỉ xác thực request, gọi application service và tạo response.
- Business rules nằm trong domain/application service, không nằm trong route hoặc ORM model.
- GitHub, model provider và Discord được đặt sau các interface để có thể fake trong test.
- Diff parser, filter, validation, deduplication và formatter là các module thuần, deterministic.
- Worker và dashboard dùng chung service layer thay vì truy cập database trực tiếp theo hai
  cách khác nhau.

### 3.3. Cấu trúc repository dự kiến

```text
auto-review-discord/
├── app/
│   ├── api/
│   │   ├── dependencies.py
│   │   ├── github_webhook.py
│   │   └── dashboard.py
│   ├── application/
│   │   ├── repositories.py
│   │   ├── review_runs.py
│   │   └── notifications.py
│   ├── github/
│   │   ├── auth.py
│   │   ├── client.py
│   │   ├── diff.py
│   │   └── schemas.py
│   ├── review/
│   │   ├── engine.py
│   │   ├── filters.py
│   │   ├── prompts.py
│   │   ├── schemas.py
│   │   └── validation.py
│   ├── models/
│   │   ├── base.py
│   │   └── openai.py
│   ├── notifications/
│   │   └── discord.py
│   ├── storage/
│   │   ├── models.py
│   │   ├── repositories.py
│   │   └── queue.py
│   ├── config.py
│   ├── logging.py
│   ├── worker.py
│   └── main.py
├── migrations/
├── templates/
├── static/
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── .env.example
├── pyproject.toml
└── README.md
```

## 4. Kế hoạch triển khai theo Pull Request

### PR 1 — Project foundation

#### Công việc

- Khởi tạo Git và cấu trúc Python.
- Cấu hình FastAPI, Pydantic, SQLAlchemy, Alembic, HTTPX, Jinja và HTMX.
- Tạo `Settings` đọc environment và kiểm tra các secret bắt buộc.
- Logging dạng JSON với bộ lọc dữ liệu nhạy cảm.
- Thiết lập Ruff, Pyright và Pytest.
- Thêm `.env.example`, README, pre-commit và CI.
- Tạo health endpoint cho API.

#### Tiêu chí hoàn thành

- API khởi động thành công bằng một command được ghi trong README.
- Migration có thể chạy trên database rỗng.
- Health check trả về thành công.
- Lint, type check và test chạy được trong CI.

### PR 2 — Domain model và persistence

#### Công việc

- Tạo các bảng:
  - `github_installations`
  - `repositories`
  - `review_configs`
  - `webhook_deliveries`
  - `review_runs`
  - `findings`
  - `notification_deliveries`
- Định nghĩa state machine cho review run và notification.
- Tạo repository interfaces và SQLAlchemy implementations.
- Tạo unique constraints cho GitHub repository, webhook delivery và PR head SHA.
- Bật SQLite foreign keys, WAL mode và busy timeout.
- Tạo migration đầu tiên.

#### Tiêu chí hoàn thành

- Một GitHub delivery không thể được ghi hai lần.
- Một commit của cùng PR không thể tạo hai review run.
- State transition không hợp lệ bị từ chối.
- Repository đổi tên không làm mất liên kết vì dùng `github_repository_id`.

### PR 3 — Diff parser, filter và chunking

#### Công việc

- Parse unified diff thành file, hunk và line map.
- Phân biệt old line và new line.
- Chỉ cho phép finding neo vào dòng hợp lệ phía HEAD.
- Áp dụng ignore rules cho:
  - binary files
  - certificates và secret files
  - lock files
  - generated/vendor files
  - repository-specific patterns
- Áp dụng `max_files`, `max_diff_lines` và token budget.
- Chunk theo file/hunk; không cắt giữa một hunk nếu còn cách tránh.
- Ghi metadata về file/dòng bị bỏ qua hoặc cắt ngắn.

#### Tiêu chí hoàn thành

- Fixture tests bao phủ add, modify, delete, rename và binary file.
- Parse đúng diff có nhiều hunk.
- Finding trỏ tới dòng không tồn tại bị loại bỏ.
- Diff vượt giới hạn trả về partial-review metadata.

### PR 4 — Review engine và model adapter

#### Công việc

- Định nghĩa interface `ModelProvider`.
- Tạo `FakeModelProvider` cho test và CLI.
- Định nghĩa Pydantic schema cho structured output.
- Implement OpenAI adapter bằng strict JSON Schema output.
- Viết prompt mặc định và cơ chế ghép custom instructions.
- Validate:
  - severity
  - confidence
  - file path
  - line number
  - số findings
- Dedupe findings và sắp xếp theo severity/confidence.
- Tính overall risk bằng rule deterministic.
- Lưu token usage, latency, model và prompt version.
- Tạo CLI nhận fixture diff để chạy review thủ công.

#### Tiêu chí hoàn thành

- CLI nhận một fixture diff và tạo kết quả review hợp lệ.
- Output sai schema không làm worker crash và có error code rõ ràng.
- Finding giả hoặc không thuộc diff bị loại bỏ.
- Tests không gọi model API thật.

### PR 5 — Discord notifier

#### Công việc

- Tạo formatter cho overview embed và finding embeds.
- Chia kết quả thành nhiều message nếu vượt giới hạn Discord.
- Luôn gửi `allowed_mentions: {"parse": []}`.
- Validate và allowlist Discord webhook URL.
- Retry có exponential backoff và jitter cho timeout, `429` và lỗi `5xx`.
- Không retry lỗi cấu hình `4xx` không thể phục hồi.
- Lưu trạng thái delivery và message ID khi có.
- Hỗ trợ resend mà không tạo review run mới.

#### Tiêu chí hoàn thành

- Payload không vượt giới hạn Discord.
- Text chứa `@everyone`, `@here` hoặc user mention không tạo mention.
- Webhook URL không xuất hiện trong log hoặc API response.
- Formatter có snapshot/golden tests.

### PR 6 — Database worker

#### Công việc

- Poll và claim job bằng transaction nguyên tử.
- Implement lease, heartbeat và stale-job recovery.
- Retry policy riêng cho GitHub API, model API và Discord.
- Phân biệt permanent failure với transient failure.
- Kiểm tra HEAD hiện tại trước khi gửi Discord.
- Hỗ trợ graceful shutdown.
- Thiết lập giới hạn concurrency và số model call trên mỗi run.

#### Tiêu chí hoàn thành

- Hai worker chạy đồng thời không xử lý cùng một review run.
- Job bị gián đoạn có thể được reclaim sau khi lease hết hạn.
- Run cũ không gửi notification khi PR đã có HEAD mới.
- Quá số retry chuyển sang `failed` với lỗi đã được redaction.

### PR 7 — GitHub App và webhook API

#### Công việc

- Tạo GitHub App JWT và installation access token.
- Implement GitHub client lấy PR metadata và file list có pagination.
- Verify `X-Hub-Signature-256` trên raw request body bằng constant-time comparison.
- Validate event, action, content type và payload size.
- Xử lý:
  - `pull_request.opened`
  - `pull_request.reopened`
  - `pull_request.synchronize`
  - `pull_request.ready_for_review`
  - installation lifecycle events
- Trong một transaction, ghi webhook delivery và enqueue review run.
- Trả response nhanh, không gọi GitHub/model/Discord trong request webhook.

#### Tiêu chí hoàn thành

- Chữ ký sai hoặc thiếu bị từ chối.
- Event/action không hỗ trợ được bỏ qua an toàn.
- Webhook hợp lệ trả về `202` và tạo đúng một run.
- Redelivery không tạo run mới.
- Draft PR tuân theo cấu hình repository.

### PR 8 — Dashboard và authorization

#### Công việc

- Implement GitHub login/session.
- Trang repositories, repository settings và review runs.
- Kiểm tra quyền trước mọi thao tác đọc/sửa nhạy cảm.
- Thêm CSRF protection.
- Secret fields dùng write-only semantics.
- Tách “retry review” và “resend Discord” thành hai thao tác rõ ràng.
- Hiển thị partial, skipped, superseded và lỗi đã redaction.

#### Tiêu chí hoàn thành

- User không có quyền không thể đọc hoặc sửa cấu hình repository khác.
- Dashboard không trả lại secret đã lưu.
- Thay đổi cấu hình được validate ở server.
- Review history hiển thị trạng thái, token usage, thời gian và findings.

### PR 9 — Hardening và acceptance tests

#### Công việc

- Tạo end-to-end tests với fake GitHub, model và Discord servers.
- Test hai repository dùng hai Discord webhook khác nhau.
- Test synchronize liên tiếp và webhook redelivery.
- Test prompt injection nằm trong PR title/body/diff.
- Test secret không xuất hiện trong log hoặc response.
- Thu thập metrics:
  - queue latency
  - review duration
  - model calls và token usage
  - retry/failure count
  - Discord delivery latency
- Tạo Dockerfile và cách chạy API/worker riêng.
- Viết runbook cho migration, key rotation và xử lý job bị kẹt.

#### Tiêu chí hoàn thành

- Toàn bộ tiêu chí MVP trong tài liệu thiết kế có automated test hoặc checklist xác minh.
- API và worker có thể chạy độc lập.
- Không có network call thật trong test suite mặc định.
- Có hướng dẫn triển khai và rollback migration.

## 5. Chiến lược kiểm thử

### Unit tests

Ưu tiên cho các module deterministic:

- Webhook signature verification.
- State transitions và idempotency key.
- Diff parsing, filtering và chunking.
- Structured output validation.
- Line validation và finding deduplication.
- Risk calculation.
- Discord formatting và secret redaction.

### Integration tests

- FastAPI routes với database SQLite tạm.
- SQLAlchemy repositories và migrations.
- Queue claim/lease với nhiều worker.
- HTTP adapters dùng mock server thay vì mock implementation detail.

### End-to-end tests

```text
fake GitHub webhook
        → API
        → SQLite queue
        → worker
        → fake GitHub API
        → fake model API
        → fake Discord webhook
```

E2E tests phải kiểm tra record trong database và payload Discord cuối cùng.

## 6. Các quyết định cần chốt trước khi bắt đầu

Để tránh đổi kiến trúc giữa chừng, cần chốt các lựa chọn sau trong PR 1:

1. Phiên bản Python tối thiểu.
2. OpenAI là provider đầu tiên hay cần provider khác ngay trong MVP.
3. Dashboard dùng GitHub OAuth ngay từ đầu hay chạy single-operator trong milestone đầu.
4. Master encryption key được cấp bằng environment variable hay secret manager của nền tảng.
5. Chính sách file mặc định: lock/generated/vendor files bị bỏ qua hoàn toàn hay chỉ ưu tiên thấp.
6. Finding chỉ được neo vào added line hay chấp nhận context line phía HEAD.
7. Review run cũ được đánh dấu `superseded` hay dùng `skipped` kèm reason.

Khuyến nghị mặc định:

- Python 3.12 trở lên.
- OpenAI là provider đầu tiên nhưng giữ `ModelProvider` interface.
- GitHub OAuth trước khi mở dashboard ra ngoài localhost.
- Master key qua environment cho MVP, thiết kế có `key_version` để hỗ trợ rotation.
- Bỏ qua binary, secret, vendor và generated files; lock files chỉ review khi có cấu hình bật.
- Cho phép finding neo vào added line hoặc context line phía HEAD.
- Dùng trạng thái `superseded` riêng để vận hành và thống kê rõ ràng.

## 7. Milestones

### Milestone A — Chứng minh review engine

Bao gồm PR 1 đến PR 4.

Kết quả cần đạt: chạy CLI trên fixture diff, nhận structured findings hợp lệ và đo được token
cùng thời gian xử lý.

### Milestone B — Pipeline tự động

Bao gồm PR 5 đến PR 7.

Kết quả cần đạt: GitHub webhook tự tạo một review run duy nhất, worker xử lý và gửi kết quả
đến đúng Discord webhook.

### Milestone C — Multi-repository MVP

Bao gồm PR 8 và PR 9.

Kết quả cần đạt: dashboard có phân quyền, quản lý được ít nhất hai repository, có lịch sử,
retry/resend và đầy đủ guardrails cần thiết.

## 8. Definition of Done cho MVP

MVP hoàn thành khi:

- Kết nối được ít nhất hai repository với hai Discord webhook khác nhau.
- PR mới hoặc commit mới tạo đúng một review run cho mỗi `head_sha`.
- Webhook redelivery không tạo review trùng.
- Finding chỉ tham chiếu file và dòng hợp lệ trong diff.
- Partial review được hiển thị rõ.
- Run lỗi có trạng thái, error code và thông báo đã redaction.
- Run cũ không gửi Discord sau khi PR có commit mới.
- Discord payload không tạo mention ngoài ý muốn.
- Không có secret trong log, dashboard response hoặc error message.
- Có deterministic tests cho webhook validation, idempotency, queue leasing, diff filtering,
  output parsing và Discord formatting.
- API, worker và migration có hướng dẫn vận hành rõ ràng.

## 9. Tài liệu kỹ thuật tham khảo

- [GitHub webhook events and payloads](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- [GitHub webhook signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [GitHub Pull Request API](https://docs.github.com/en/rest/pulls/pulls)
- [Discord Webhook API](https://docs.discord.com/developers/resources/webhook)
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)

