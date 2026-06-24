# Báo cáo thay đổi phần chunking cho dữ liệu DOFFICE

Tài liệu này mô tả trạng thái mới nhất của phần chia nhỏ dữ liệu sau khi lấy văn bản từ kho DOFFICE.

Mục tiêu của lần chỉnh sửa hiện tại là làm cho chunk dễ đọc hơn, ít bị mất ngữ cảnh hơn và phù hợp hơn với văn bản hành chính có cấu trúc mục như `1.`, `1.1.`, `2.`, `Điều 1`, `Khoản 1`. Ngoài ra, các chunk bảng vẫn giữ hướng xử lý theo bảng, dòng, nhóm dòng và cột như trước.

Các thay đổi chỉ áp dụng cho nhánh tài liệu DOFFICE. Luồng upload file thông thường không bị thay đổi.

## 1. Pipeline tổng quan hiện tại

Pipeline DOFFICE hiện tại có thể hiểu như sau:

```text
Nhập id_vb
-> lấy JSON từ kho DOFFICE
-> lấy nội dung chính và metadata văn bản
-> nhận diện bảng HTML và bảng Markdown
-> bảo vệ bảng trước khi làm sạch text
-> làm sạch nội dung ngoài bảng
-> tách phần chữ ký/cuối văn bản
-> chia text theo mục/tiểu mục nếu nhận diện được
-> gộp mục cha ngắn vào các mục con để không mất ngữ cảnh
-> tạo chunk text có thông tin văn bản ở đầu
-> tạo chunk bảng có thông tin văn bản và tên bảng/phụ lục ở đầu
-> lưu chunk vào PostgreSQL
-> embedding/index vào Qdrant và Elasticsearch như luồng hiện tại
```

Điểm quan trọng là hệ thống không chỉ cắt văn bản theo độ dài nữa. Với phần text thường, hệ thống cố gắng chia theo cấu trúc nội dung. Với phần bảng, hệ thống vẫn xử lý bảng như dữ liệu có cấu trúc riêng.

## 2. Các file đã thay đổi

### 2.1. `backend/app/services/doffice_content_normalizer.py`

File này là nơi chuẩn hóa nội dung DOFFICE trước khi tạo chunk.

Các phần đang xử lý trong file này:

- Làm sạch nội dung lấy từ DOFFICE.
- Nhận diện bảng HTML.
- Nhận diện bảng Markdown.
- Bảo vệ bảng trước khi làm sạch text.
- Gắn ngữ cảnh trước bảng vào bảng.
- Tạo các phần trung gian như `document_header`, `document_summary`, `document_body`, `table_parent`, `table_row`, `table_group`, `table_column`.
- Chia phần text thân văn bản theo mục/tiểu mục.
- Gộp mục cha ngắn vào mục con thay vì tạo một chunk cha quá ngắn.
- Giữ mục cha thành chunk riêng nếu mục cha có nội dung đáng kể.
- Giữ nội dung phụ lục trong chunk text, không cắt bỏ từ dòng `Phụ lục` trở đi.
- Xem `Phụ lục 01`, `Phụ lục 02` là mục cha của các mục bên trong như `1. Mục tiêu`, `2. Nội dung`.
- Nhận diện quan hệ cha-con linh hoạt: nếu trong văn bản có `Điều 1` thì `1.`, `2.`... ngay sau nó tự động được xem là mục con của `Điều`; nếu không có `Điều` thì `1.`, `2.`... tự động xem là mục cha cấp cao nhất.
- Khi tạo ngữ cảnh bảng, lấy theo khối phụ lục/heading gần nhất thay vì chỉ lấy vài dòng sát bảng.
- Giữ cấu trúc bảng bằng Markdown table sạch.
- Sửa một số lỗi chữ bị mojibake/OCR đã biết.

### 2.2. `backend/app/services/doffice_chunking.py`

File này chuyển các phần trung gian thành chunk thật sự để lưu vào PostgreSQL và đưa đi embedding/index.

Các phần mới/cần chú ý:

- Thêm phần đầu cho chunk text:
  - `Văn bản`
  - `Ngày ban hành`
  - `Cơ quan ban hành`
- Thêm phần đầu cho chunk bảng:
  - `Văn bản`
  - `Phụ lục/Bảng`
  - `Bảng` nếu cần
  - `STT` nếu là chunk dòng bảng
- Giữ thêm metadata về mục, đường dẫn heading và phần chia nhỏ nếu một mục quá dài.

### 2.3. `backend/tests/test_doffice_ingestion.py`

File test được bổ sung để kiểm tra:

- Text body được chia theo các mục như `1.`, `1.1.`, `2.`.
- Mục cha ngắn được đưa vào các mục con để chunk con vẫn đủ ngữ cảnh.
- Mục cha có nội dung đáng kể vẫn được giữ thành chunk riêng.
- Phụ lục không bị loại khỏi body chunk.
- Mục con trong phụ lục giữ được đường dẫn phụ lục cha.
- Chunk text có phần đầu gồm tên văn bản, ngày ban hành và cơ quan ban hành.
- Metadata của chunk text giữ được đường dẫn mục cha - mục con.
- Các test bảng cũ vẫn chạy được.

## 3. Phương pháp xử lý text sau thay đổi

### 3.1. Trước đây

Trước đây phần thân văn bản thường được gom thành một `document_body` lớn, sau đó các bước sau có thể cắt theo độ dài.

Cách này có nhược điểm:

- Một chunk có thể bị cắt giữa chừng.
- Mục `1` có thể bị dính với mục `2`.
- Mục `1.1` có thể bị tách khỏi mục cha `1`.
- Khi xem lại chunk trong PostgreSQL hoặc giao diện, người đọc khó biết chunk đang thuộc phần nào của văn bản.

### 3.2. Hiện tại

Hiện tại phần thân văn bản được chia theo cấu trúc mục trước.

Hệ thống nhận diện các kiểu mục phổ biến và **xếp cấp độ dựa trên ngữ cảnh**:

**Cấp 1 (mục cha cao nhất):**
- `1. Mục tiêu` (nếu không có `Điều` trong văn bản)
- `2. Tổ chức thực hiện`
- `I. Nội dung chung`
- `II. Kế hoạch triển khai`
- `Điều 1`, `Điều 2`,...
- `Phụ lục 01`
- `Phụ lục 02`

**Cấp 2 (mục con):**
- `1.1. Phạm vi thực hiện`
- `1.2. Yêu cầu dữ liệu`
- `1. Mục tiêu` (nếu trong văn bản **có** `Điều` — tự động trở thành con của `Điều`)
- `Khoản 1`
- `Mục 1`

**Cấp 3 trở lên:**
- `1.1.1. Nội dung chi tiết`
- `1.1.1.1. Yêu cầu cụ thể`

**Quy tắc xếp cấp linh hoạt:**
- `Điều 1` luôn là cấp 1.
- Nếu trong stack heading có `Điều 1`, thì các mục số như `1.`, `2.`, `3.`... ngay sau đó tự động được đẩy xuống **cấp 2** (làm con của `Điều 1`).
- Nếu văn bản không có `Điều` thì `1.`, `2.`... là **cấp 1** (làm cha của `1.1`, `1.2`...).
- `Phụ lục` luôn là cấp 1. Các mục số bên trong phụ lục như `1. Mục tiêu` tự động thành cấp 2 (con của phụ lục), không bị ảnh hưởng bởi luật `Điều`.

Nhờ cơ chế này, cùng một heading `1. Mục tiêu` có thể là cha hoặc con tùy theo văn bản:
- Văn bản có `Điều 1` → `1. Mục tiêu` là con của `Điều 1` (cấp 2).
- Văn bản không có `Điều` → `1. Mục tiêu` là cha của `1.1`, `1.2` (cấp 1).

Nếu nhận diện được các mục này, hệ thống không còn máy móc tạo mỗi heading thành một chunk riêng. Thay vào đó, hệ thống xét quan hệ mục cha - mục con.

Quy tắc gộp cha-con:

- Nếu mục cha chỉ là tiêu đề hoặc chỉ có vài dòng giới thiệu ngắn, mục cha sẽ không đứng thành chunk riêng.
- Nội dung ngắn của mục cha sẽ được đưa vào các mục con phía dưới.
- Nếu mục cha có nội dung đáng kể, mục cha vẫn được giữ thành một chunk riêng.
- Mục con vẫn giữ đường dẫn mục cha trong metadata để biết nó thuộc phần nào.

Một mục cha được xem là có nội dung đáng kể khi rơi vào một trong các trường hợp:

- phần nội dung của mục cha dài khoảng từ 300 ký tự trở lên;
- mục cha có từ 2 đoạn văn trở lên;
- mục cha có danh sách gạch đầu dòng;
- mục cha có ngày tháng, số liệu, tiền, phần trăm;
- mục cha có nội dung mang tính yêu cầu, điều kiện, thời hạn, trách nhiệm hoặc mốc hoàn thành.

Ví dụ:

```text
1. Mục tiêu
Triển khai chuẩn hóa dữ liệu GIS.

1.1. Phạm vi thực hiện
Nội dung phạm vi...

1.2. Yêu cầu dữ liệu
Nội dung yêu cầu...
```

Vì mục `1. Mục tiêu` ngắn và có các mục con `1.1`, `1.2`, hệ thống không tạo một chunk riêng chỉ cho mục `1`. Thay vào đó, mục cha được đưa vào từng mục con:

```text
Chunk document_body 1:
1. Mục tiêu
Triển khai chuẩn hóa dữ liệu GIS.
1.1. Phạm vi thực hiện
Nội dung phạm vi...

Chunk document_body 2:
1. Mục tiêu
Triển khai chuẩn hóa dữ liệu GIS.
1.2. Yêu cầu dữ liệu
Nội dung yêu cầu...
```

Nếu mục cha có nội dung dài hoặc có thông tin quan trọng, hệ thống giữ mục cha thành chunk riêng. Các mục con vẫn được thêm tên mục cha ở đầu để không bị mất ngữ cảnh, nhưng không lặp lại toàn bộ nội dung dài của mục cha.

Ví dụ:

```text
3. Tổ chức thực hiện
Các đơn vị phải hoàn thành rà soát dữ liệu trước ngày 30/09/2025...

3.1. CPCIT
CPCIT chuẩn bị nền tảng...

3.2. Các đơn vị
Các đơn vị rà soát và gửi dữ liệu...
```

Sẽ tạo ra:

```text
Chunk document_body 1:
3. Tổ chức thực hiện
Các đơn vị phải hoàn thành rà soát dữ liệu trước ngày 30/09/2025...

Chunk document_body 2:
3. Tổ chức thực hiện
3.1. CPCIT
CPCIT chuẩn bị nền tảng...

Chunk document_body 3:
3. Tổ chức thực hiện
3.2. Các đơn vị
Các đơn vị rà soát và gửi dữ liệu...
```

Nếu một mục quá dài, hệ thống mới chia nhỏ tiếp bên trong mục đó. Khi chia nhỏ, metadata vẫn giữ thông tin mục gốc để biết các phần nhỏ đó thuộc cùng một mục.

Nếu văn bản không có cấu trúc mục rõ ràng, hệ thống fallback về cách cũ: tạo body chunk từ nội dung thân văn bản đã làm sạch.

### 3.3. Giữ phụ lục trong chunk text

Trước đây có một bước loại bỏ phần thân văn bản từ dòng `PHỤ LỤC` hoặc `PHU LUC` trở đi. Cách này giúp tránh bảng phụ lục bị trộn vào body, nhưng lại gây lỗi với những tài liệu mà nội dung chính nằm trong phụ lục.

Ví dụ người dùng hỏi:

```text
Mục tiêu của Phụ lục 02 là gì?
```

Nếu phần `Phụ lục 02` đã bị cắt khỏi body, hệ thống không có chunk text chứa:

```text
Phụ lục 02
1. Mục tiêu
...
```

Khi đó retrieval có thể chỉ lấy được `Phụ lục 01` hoặc các bảng khác, làm LLM kết luận sai rằng tài liệu không có Phụ lục 02.

Sau thay đổi, hệ thống chỉ bỏ placeholder bảng như `[[TABLE_1]]`, không cắt bỏ phần phụ lục. Vì vậy các nội dung như `Phụ lục 02`, `1. Mục tiêu`, `2. Nội dung thực hiện` vẫn được tạo thành chunk text.

Ngoài ra, nếu trong phụ lục có các mục con như:

```text
Phụ lục 02
MÔ TẢ DỮ LIỆU KHỞI TẠO...

1. Mục tiêu
...
```

thì chunk của `1. Mục tiêu` sẽ được gắn thêm thông tin phụ lục cha ở đầu. Nhờ vậy khi retrieval lấy chunk mục tiêu, LLM vẫn biết mục tiêu đó thuộc `Phụ lục 02`, không bị nhầm sang phụ lục khác.

### 3.4. Lấy ngữ cảnh bảng theo khối cấu trúc

Trước đây ngữ cảnh bảng chủ yếu lấy một số dòng gần nhất nằm ngay trước bảng. Cách này dễ sai với các tài liệu có bố cục dài, ví dụ:

```text
Phụ lục 02
MÔ TẢ DỮ LIỆU KHỞI TẠO...

1. Mục tiêu
- Khởi tạo khung CSDL GIS...
- Chuyển đổi dữ liệu ban đầu cho 07 đối tượng...
(1) F08_CotDien_HT – Lớp cột điện;
(2) F09_DuongDay_HT – Lớp đường dây;
...
(7) F10_TuPhanPhoi_HT – Lớp tủ phân phối.

| TT | Tên trường | Mô tả |
```

Nếu chỉ lấy vài dòng sát bảng, ngữ cảnh có thể bắt đầu từ `(2)` hoặc `(3)`, làm mất `Phụ lục 02`, mất `1. Mục tiêu`, và mất cả dòng `(1)`. Khi đó bảng vẫn được parse nhưng LLM không biết bảng thuộc phụ lục nào và mục tiêu nào.

Sau thay đổi, khi tạo ngữ cảnh bảng, hệ thống làm theo hướng tổng quát hơn:

```text
tìm các dòng trước bảng
-> bỏ dòng rác, placeholder bảng, dòng markdown table
-> tìm mốc cấu trúc gần nhất, ưu tiên Phụ lục
-> nếu không có Phụ lục thì dùng heading gần nhất
-> lấy nội dung từ mốc đó đến trước bảng
-> nếu đoạn quá dài thì giữ phần đầu quan trọng và phần sát bảng
```

Cách này không hardcode rằng phụ lục phải nằm ngay trước bảng. Miễn là bảng đang nằm trong một khối phụ lục hoặc một mục đã nhận diện được, ngữ cảnh bảng sẽ cố giữ lại phần đầu của khối đó.

Lợi ích:

- bảng trong `Phụ lục 02` vẫn có ngữ cảnh `Phụ lục 02`;
- mục như `1. Mục tiêu` không bị rơi khỏi ngữ cảnh bảng;
- các dòng liệt kê quan trọng như `(1) F08_CotDien_HT` không bị mất chỉ vì nằm xa bảng hơn vài dòng;
- phù hợp hơn với nhiều kiểu thiết kế tài liệu khác nhau.

## 4. Phần đầu của chunk text

Với chunk text, hệ thống thêm phần đầu ngắn trước nội dung chính.

Ví dụ:

```text
Văn bản: 6515/EVNCPC-VTCNTT+KD+KT - Kế hoạch xây dựng hệ thống GIS chuẩn hóa cơ sở dữ liệu lưới điện của EVNCPC
Ngày ban hành: 21/08/2025
Cơ quan ban hành: Tổng công ty Điện lực miền Trung

1. Mục tiêu
...
```

Ý nghĩa:

- `Văn bản`: cho biết chunk thuộc văn bản nào.
- `Ngày ban hành`: giúp LLM trả lời các câu hỏi liên quan thời điểm ban hành.
- `Cơ quan ban hành`: giúp LLM biết đơn vị/cơ quan tạo ra văn bản.

Lợi ích:

- Khi retrieval lấy một chunk rời rạc, LLM vẫn biết ngữ cảnh văn bản.
- Khi xem chunk trong PostgreSQL, người đọc dễ đánh giá chunk hơn.
- Câu hỏi hành chính như “văn bản này do ai ban hành”, “ngày ban hành là khi nào” có cơ hội trả lời tốt hơn.

## 5. Phần đầu của chunk bảng

Với chunk bảng, hệ thống thêm phần đầu phù hợp với bảng.

Ví dụ:

```text
Văn bản: 6515/EVNCPC-VTCNTT+KD+KT - Kế hoạch xây dựng hệ thống GIS chuẩn hóa cơ sở dữ liệu lưới điện của EVNCPC
Phụ lục/Bảng: Phụ lục 01 - Phương án sáp nhập dữ liệu GIS
Bảng: Bảng 1
STT: 1
```

Ý nghĩa:

- `Văn bản`: bảng thuộc văn bản nào.
- `Phụ lục/Bảng`: bảng nằm trong phụ lục hoặc phần nào.
- `Bảng`: tên bảng cụ thể nếu tài liệu có tên bảng; nếu không có thì dùng `Bảng 1`, `Bảng 2`, `Bảng 3` theo thứ tự xuất hiện.
- `STT`: dùng cho chunk từng dòng bảng.

Lợi ích:

- Dòng bảng không bị mất ngữ cảnh.
- Bảng trong phụ lục dễ phân biệt với bảng ở phần khác.
- Khi retrieval lấy một dòng bảng, LLM biết dòng đó thuộc bảng/phụ lục nào.
- Không để lộ nhãn nội bộ của hệ thống như `DOffice` vào nội dung trả lời.

## 6. Các loại chunk hiện tại

### 6.1. `document_header`

Chunk này chứa thông tin đầu văn bản.

Thường gồm:

- số/ký hiệu văn bản;
- ngày văn bản;
- trích yếu;
- nơi ban hành;
- người ký.

Dùng cho câu hỏi hành chính về văn bản.

### 6.2. `document_summary`

Chunk này được tạo nếu DOFFICE có trường tóm tắt.

Hiện tại chunk summary cũng được thêm phần đầu văn bản để không mất ngữ cảnh.

### 6.3. `document_body`

Chunk này là phần text chính của văn bản.

Sau thay đổi, `document_body` không còn mặc định là một cục lớn. Nếu nhận diện được mục/tiểu mục, hệ thống chia theo cấu trúc mục trước, sau đó xử lý thêm quan hệ mục cha - mục con.

Cách xử lý hiện tại:

- Mục không có mục con sẽ được tạo thành chunk riêng.
- Mục cha ngắn sẽ không đứng riêng, mà được đưa vào các mục con để giữ ngữ cảnh.
- Mục cha có nội dung đáng kể sẽ đứng thành chunk riêng.
- Mục con của một mục cha quan trọng vẫn có tên mục cha ở đầu chunk, nhưng không lặp lại toàn bộ nội dung dài của mục cha.
- Nếu một mục quá dài, hệ thống mới chia nhỏ tiếp theo độ dài.

Metadata quan trọng:

- `section_title`: tên mục hiện tại.
- `heading_path`: đường dẫn mục cha - mục con. Ví dụ `["Điều 1", "1. Thông tin về gói thầu", "1.1. Tên gói thầu"]`.
- `section_index`: thứ tự mục.
- `section_part`: phần nhỏ của mục nếu mục quá dài và bị chia tiếp.

### 6.4. `table_parent`

Chunk tổng quan bảng.

Nó cho biết:

- bảng tên gì;
- có bao nhiêu dòng;
- có những cột nào;
- bảng nằm trong ngữ cảnh nào;
- một số dòng đầu của bảng dưới dạng Markdown table sạch.

Dùng tốt cho câu hỏi tổng quan về bảng.

### 6.5. `table_row`

Chunk từng dòng bảng.

Nó giữ quan hệ ngang trong bảng, tức là các ô nằm trên cùng một dòng.

Dùng khi người dùng hỏi về một dòng, một đối tượng hoặc một hạng mục cụ thể trong bảng.

### 6.6. `table_group`

Chunk nhóm dòng bảng.

Nếu bảng có nhóm logic như nền tảng/giai đoạn thì nhóm theo logic đó. Nếu không có nhóm rõ ràng thì nhóm theo khoảng dòng, ví dụ:

```text
Rows 1-10
Rows 11-20
Rows 21-30
```

Dùng khi câu hỏi cần nhiều dòng liên quan nhưng không cần toàn bộ bảng.

### 6.7. `table_column`

Chunk theo cột.

Chunk này gom nội dung của một cột theo nhiều dòng. Nó giúp LLM đọc bảng theo chiều dọc.

Ví dụ với bảng có cột `CPCIT` và `Các CTDL`, chunk theo cột giúp LLM đọc riêng nội dung thuộc `CPCIT` mà không bị lẫn sang cột bên cạnh.

Hiện tại hệ thống tạo `table_column` cho các cột có tên cột và có nội dung. Không dùng danh sách từ khóa để quyết định cột nào được sinh.

### 6.8. `footer_signature`

Chunk phần chữ ký/cuối văn bản.

Chunk này được đánh dấu không index hoặc không embedding nếu chỉ là footer/chữ ký.

## 7. Phương pháp xử lý bảng

Phần bảng vẫn giữ các cải tiến trước đó.

### 7.1. Nhận diện bảng HTML và bảng Markdown

Hệ thống xử lý được cả:

```html
<table>
  <tr><td>...</td></tr>
</table>
```

và:

```markdown
| TT | Tên trường | Mô tả |
| --- | --- | --- |
| 1 | ID | Mã định danh |
```

Sau khi parse, hai loại bảng này được đưa về cùng một dạng chung để tạo chunk.

### 7.2. Bảo vệ bảng trước khi làm sạch text

Bảng được nhận diện trước, sau đó thay bằng placeholder khi làm sạch phần text thường.

Nhờ vậy:

- bảng không bị trộn vào chunk text;
- dấu `|` của Markdown table không bị làm hỏng;
- thẻ HTML table không bị xóa nhầm trước khi parse.

### 7.3. Giữ cấu trúc bảng bằng Markdown table sạch

Bảng trong chunk được xuất lại thành Markdown table sạch.

Ví dụ:

```markdown
| TT | Dữ liệu | CPCIT | Các CTDL |
| --- | --- | --- | --- |
| 1 | GIS 110kV | Nội dung của CPCIT | Nội dung của CTDL |
```

Cách này giúp LLM nhìn rõ quan hệ cột - dòng hơn so với plain text phẳng.

## 8. Sửa lỗi mojibake/OCR có kiểm soát

Trong một số tài liệu, có một vài cụm chữ bị lỗi encoding hoặc OCR.

Ví dụ:

```text
Chʼյ đổi sang GIS
```

được sửa thành:

```text
Chuyển đổi sang GIS
```

Cách làm hiện tại là chỉ sửa các cụm lỗi đã biết. Hệ thống không tự ý sửa toàn bộ tiếng Việt trong văn bản.

Lý do:

- tránh làm sai chữ đang đúng;
- giảm rủi ro sửa nhầm nội dung;
- chỉ xử lý các lỗi thực tế đã thấy trong dữ liệu.

Nếu sau này dữ liệu xuất hiện nhiều lỗi encoding hơn, có thể mở rộng bảng sửa lỗi hoặc bổ sung bước nhận diện mojibake tổng quát hơn.

## 9. Khác gì so với pipeline trước

### Trước đây

```text
Nội dung DOFFICE
-> làm sạch
-> tạo một document_body lớn
-> tạo chunk bảng
-> embedding/index
```

Hạn chế:

- chunk text có thể bị cắt lở dở;
- mục cha/mục con dễ bị dính hoặc tách sai;
- chunk text thiếu thông tin văn bản ở đầu;
- khi xem lại chunk khó biết đoạn đó thuộc văn bản nào;
- LLM có thể thiếu ngữ cảnh khi chỉ lấy được một chunk nhỏ.

### Hiện tại

```text
Nội dung DOFFICE
-> làm sạch có kiểm soát
-> nhận diện bảng
-> tách text khỏi bảng
-> chia text theo mục/tiểu mục
-> xác định cấp độ heading linh hoạt theo ngữ cảnh
   - nếu có Điều → 1., 2. làm con của Điều
   - nếu không có Điều → 1., 2. làm cha
-> giữ nội dung phụ lục và gắn phụ lục làm cha của mục bên trong
-> gộp mục cha ngắn vào mục con
-> thêm thông tin văn bản ở đầu chunk text
-> thêm thông tin văn bản + bảng/phụ lục ở đầu chunk bảng
-> tạo chunk bảng theo tổng quan/dòng/nhóm/cột
-> embedding/index
```

Điểm khác biệt lớn nhất:

- text body được chia theo cấu trúc mục;
- **xếp cấp heading linh hoạt**: `1. Mục tiêu` tự động làm con của `Điều 1` nếu có `Điều`, làm cha của `1.1` nếu không có `Điều`;
- nội dung phụ lục không còn bị cắt khỏi chunk text;
- các mục như `1. Mục tiêu` bên trong `Phụ lục 02` giữ được ngữ cảnh phụ lục cha;
- mục cha ngắn được gộp vào mục con để giữ ngữ cảnh và giảm chunk dư;
- mục cha có nội dung đáng kể vẫn được giữ thành chunk riêng;
- mỗi chunk text có tên văn bản, ngày ban hành, cơ quan ban hành;
- mỗi chunk bảng có tên văn bản và bảng/phụ lục;
- metadata của text chunk có `section_title`, `heading_path` (ví dụ `["Điều 1", "1. Nội dung"]`);
- chunk dễ đọc hơn khi xem trong PostgreSQL hoặc giao diện;
- LLM có nhiều ngữ cảnh hơn khi trả lời.

## 10. Cách kiểm tra trong PostgreSQL

Xem các chunk theo `id_vb`:

```sql
select
    c.chunk_index,
    c.metadata->>'chunk_type' as chunk_type,
    c.metadata->>'section_title' as section_title,
    c.metadata->'heading_path' as heading_path,
    c.metadata->>'table_name' as table_name,
    c.metadata->>'row_number' as row_number,
    c.metadata->>'column_name' as column_name,
    length(c.content) as content_length,
    c.content
from chunks c
join documents d on d.id = c.document_id
where d.document_metadata->>'id_vb' = '1068586'
order by c.chunk_index;
```

Xem riêng chunk text:

```sql
select
    c.chunk_index,
    c.metadata->>'section_title' as section_title,
    c.metadata->'heading_path' as heading_path,
    length(c.content) as content_length,
    c.content
from chunks c
join documents d on d.id = c.document_id
where d.document_metadata->>'id_vb' = '1068586'
  and c.metadata->>'chunk_type' = 'document_body'
order by c.chunk_index;
```

Xem riêng chunk bảng:

```sql
select
    c.chunk_index,
    c.metadata->>'chunk_type' as chunk_type,
    c.metadata->>'table_name' as table_name,
    c.metadata->>'row_number' as row_number,
    c.metadata->>'column_name' as column_name,
    c.content
from chunks c
join documents d on d.id = c.document_id
where d.document_metadata->>'id_vb' = '1068586'
  and c.metadata->>'chunk_type' in ('table_parent', 'table_row', 'table_group', 'table_column')
order by c.chunk_index;
```

Đếm số chunk theo loại:

```sql
select
    c.metadata->>'chunk_type' as chunk_type,
    count(*) as total
from chunks c
join documents d on d.id = c.document_id
where d.document_metadata->>'id_vb' = '1068586'
group by c.metadata->>'chunk_type'
order by total desc;
```

## 11. Nhận xét tổng thể

Hướng hiện tại tốt hơn pipeline cũ ở phần ngữ cảnh, khả năng đọc lại chunk và xử lý đa dạng cấu trúc văn bản hành chính.

Ưu điểm:

- chunk text ít bị cắt giữa chừng hơn;
- mục cha ngắn không tạo chunk dư mà được đưa vào mục con;
- mục cha quan trọng vẫn có chunk riêng để không mất thông tin;
- **xếp cấp heading linh hoạt theo ngữ cảnh**: `1.` tự động làm con của `Điều 1` nếu có `Điều`, tự làm cha nếu không có `Điều`;
- cùng một heading `1. Mục tiêu` có thể xử lý đúng vai trò cha hoặc con tùy theo văn bản;
- LLM có thêm tên văn bản, ngày ban hành và cơ quan ban hành;
- bảng vẫn giữ được cấu trúc;
- bảng có thể đọc theo cả dòng, nhóm dòng và cột;
- dễ kiểm tra chunk trong PostgreSQL hơn;
- giảm khả năng LLM trả lời lẫn giữa các văn bản hoặc phụ lục.

Điểm cần lưu ý:

- Nếu văn bản OCR quá lỗi hoặc không có đánh số mục rõ ràng, hệ thống sẽ fallback về cách chia body thông thường.
- Nếu một tài liệu có rất nhiều heading nhỏ, số lượng chunk text vẫn có thể tăng, nhưng mục cha ngắn sẽ được gộp vào mục con để giảm bớt chunk dư.
- Nếu một mục quá dài, mục đó vẫn cần chia nhỏ tiếp để tránh chunk quá lớn.

Nói ngắn gọn: pipeline hiện tại ưu tiên chia theo ý nghĩa và cấu trúc văn bản trước, giữ mục cha - mục con đủ ngữ cảnh, xếp cấp heading linh hoạt theo ngữ cảnh văn bản, chỉ dùng chia theo độ dài như phương án dự phòng.
