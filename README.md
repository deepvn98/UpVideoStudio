# UpVideo Studio 1.8.0

Ứng dụng Windows cục bộ để quản lý nội dung, playlist, lịch xuất bản và tiến trình tải video lên nhiều kênh YouTube.

## Xóa video khỏi chương trình

Chọn ít nhất một bản nháp rồi bấm **Khai báo nội dung**: thiết lập được lưu theo kênh và áp dụng cho toàn bộ bản nháp chưa bắt đầu của kênh đó. **Xếp lịch** yêu cầu một kênh cụ thể, lưu quy tắc cho kênh và chỉ cập nhật những video đang chọn **Theo thiết lập kênh**. Video có thiết lập xuất bản riêng chỉ bị thay đổi khi người dùng chủ động tick **Ghi đè**. Khi mở lại chương trình, hai hộp thoại hiển thị đúng thiết lập được lưu gần nhất của kênh đang chọn. Video lỗi, tạm dừng, đang chạy và đã tải lên được giữ nguyên.

Khi bộ lọc **Kênh** chọn một kênh cụ thể, **Khai báo nội dung** chỉ hiển thị tên playlist của kênh đó. Playlist chỉ thay đổi khi người dùng tick hoặc bỏ tick trong danh sách. Chọn **Tất cả các kênh** sẽ không hiện phần playlist. Nếu tải danh sách lỗi, các playlist đã lưu được giữ nguyên.

Khi chọn **Tất cả các kênh**, hàng đợi tự nhóm video theo thứ tự kênh trong danh sách tài khoản. Kéo biểu tượng chấm ở đầu mỗi video để đổi vị trí trong cùng kênh; chương trình chặn thả sang kênh khác. Thứ tự được lưu trên máy, quyết định thứ tự upload và tính lại giờ đăng cho các bản nháp đang dùng lịch mặc định của kênh. Lịch đặt riêng và lịch của video đã bắt đầu tải không bị ghi đè.

Người dùng có thể yêu cầu **Xóa video** ở mọi trạng thái. Với **Chờ tải lên**, **Đang tải lên** hoặc **Hoàn thiện**, chương trình dừng tác vụ an toàn rồi gỡ mục khỏi hàng đợi và dữ liệu của chương trình. File video, TXT/DOCX, thumbnail và thư mục nguồn trên máy được giữ nguyên. Video đã có trên YouTube không bị xóa hoặc hủy lịch trên YouTube.

## Cách xóa video

Chọn video → **Xóa video** → xác nhận. Nếu video đang chạy, chương trình dừng tác vụ an toàn trước khi gỡ video khỏi hàng đợi.

Xóa chỉ gỡ bản ghi video khỏi chương trình. Ví dụ, khi xóa video trong `D:\KenhA\Video01`, thư mục `Video01` cùng video, ảnh, nội dung và các file khác vẫn nằm nguyên tại chỗ. Video đã đăng không bị xóa khỏi YouTube.

Việc xóa khỏi chương trình không yêu cầu thay đổi cấu trúc thư mục nguồn.

Nếu thư mục vẫn đang liên kết, lần bấm **Cập nhật** sau đó có thể nhận diện video này là nội dung mới và thêm lại vào hàng đợi.

## Cập nhật thư mục đã liên kết

Khi nhập video cho một kênh hoặc nhiều kênh, chương trình lưu đường dẫn và cách gán nội dung làm nguồn liên kết. Chương trình không quét nền. Bấm **Cập nhật** để quét lại thư mục của kênh đang chọn; chọn **Tất cả các kênh** để quét tất cả nguồn đã liên kết.

Nút chỉ hoạt động khi không có video nào đang chờ tải lên, đang tải lên hoặc hoàn thiện. Tạm dừng mọi tác vụ và chờ chúng dừng trước khi cập nhật.

- Video mới: tạo bản nháp và áp dụng thiết lập của kênh.
- Video/TXT/DOCX/thumbnail thay đổi: thay bản nháp cũ bằng bản mới, giữ vị trí trong hàng đợi. Video không thay đổi được giữ nguyên.
- Video không còn trong nguồn: xóa mục liên quan khỏi hàng đợi và dữ liệu cục bộ của ứng dụng. File trên máy và video đã đăng trên YouTube không bị xóa.
- Video đã đăng nhưng vẫn còn trong nguồn: hiện lựa chọn đăng lại. Chỉ tạo bản nháp mới khi bạn tick xác nhận.
- Video nghi trùng trên YouTube: hiện video khớp tiêu đề/tên file để bạn xem và quyết định có thêm bản nháp hay không. Bản nháp trùng hiện có luôn được bỏ qua.
- Nếu thư mục hoặc TXT/DOCX/thumbnail có lỗi, chương trình báo đường dẫn và không áp dụng cập nhật cho đến khi quét thành công.

Với nhập nhiều kênh, thứ tự kênh được lưu cùng thư mục gốc để lần cập nhật sau gán lại đúng thư mục nội dung. Mỗi kênh chỉ có một liên kết nguồn; chọn liên kết mới sẽ thay liên kết cũ, còn các bản nháp cũ không tự bị gỡ cho đến khi bạn cập nhật liên kết mới.

Ứng dụng Python mới để chuẩn bị, xếp lịch và tải video lên YouTube. Giao diện tiếng Việt chạy trong trình duyệt trên máy, không cần Node.js hoặc cài thư viện Python bên ngoài để sử dụng. Hỗ trợ kết nối Google trên Windows 10/11 bằng mã hóa DPAPI.

## Chạy ứng dụng

**Cách nhanh:** mở `Start.cmd`. Trình khởi động ưu tiên bản độc lập tại `dist/UpVideoStudio.exe`, nếu chưa đóng gói sẽ chạy mã nguồn.

**Chạy từ mã nguồn:** cài Python 3.12 trở lên, sau đó:

```powershell
cd UpVideoStudio
python run.py
```

Trình duyệt tự mở. Bản phát hành không hiện cửa sổ terminal và chỉ lắng nghe tại `127.0.0.1`, ưu tiên cổng `8765`, sau đó chọn cổng trống khác nếu cổng này bận. Mở chương trình lần thứ hai sẽ mở lại tab của phiên đang chạy. Đóng tab không dừng tiến trình nền; dùng nút **Thoát** ở góc trên bên phải để dừng an toàn và lưu tiến trình upload. Khi chạy từ mã nguồn trong terminal, vẫn có thể nhấn Ctrl+C.

Nếu thấy mất kết nối hoặc `Failed to fetch`: mở lại `Start.cmd`/EXE và dùng tab vừa mở, không dùng địa chỉ cổng cũ. Nếu vừa khởi động lại ứng dụng tại cùng địa chỉ, tải lại trang để nhận phiên giao diện mới. Ứng dụng hiển thị hướng dẫn mất kết nối và không tự gửi lại thao tác tải lên.

Dữ liệu nằm ở `%LOCALAPPDATA%\UpVideoStudio\studio.db`, độc lập với mã nguồn và thư mục video. Không có Firebase, mã kích hoạt hoặc khóa quản trị được phân phối kèm ứng dụng.

Địa chỉ giao diện: **http://127.0.0.1:8765/** (cổng có thể khác nếu 8765 đang bận). Khởi động chương trình sẽ tự mở đúng địa chỉ IP cục bộ. Giao diện và callback Google đều sử dụng IP loopback, không dùng tên chương trình làm địa chỉ.

## Kết nối kênh

1. Trong Google Cloud, tạo project và bật **YouTube Data API v3**.
2. Cấu hình OAuth consent screen. Nếu External/Testing, thêm email dùng đăng nhập vào **Test users**.
3. Tạo **OAuth client ID → Desktop app**, tải file JSON.
4. Trong **Kênh của tôi → Kết nối kênh**, chọn JSON và mở trang Google.
5. Chọn đúng tài khoản/kênh hoặc Brand Account, cấp quyền rồi quay lại ứng dụng. Kiểm tra tên và channel ID hiện ra.

Token gắn với cả OAuth client ID và channel ID; không dùng email làm tên lưu trữ. JSON chứa token/client secret được mã hóa bằng DPAPI của tài khoản Windows hiện tại. Không chuyển dữ liệu đăng nhập sang tài khoản Windows khác. Khi mở giao diện, ứng dụng thử làm mới token cho từng kênh trong nền. Nếu Google từ chối quyền (`invalid_client`, `deleted_client`, `invalid_grant` hoặc thiếu quyền), kênh được đánh dấu **Cần kết nối lại** trên trang Kênh của tôi và có nút xử lý nhanh ở đầu giao diện. **Kết nối lại** trước tiên dùng OAuth client đã lưu. Với `invalid_client`, ứng dụng yêu cầu JSON cùng client; với `deleted_client`, cần tạo OAuth Client Desktop mới. Sau khi xác nhận đúng kênh, ứng dụng chuyển hàng đợi, lịch sử và thư mục liên kết sang client mới. Không thể chuyển trong lúc kênh đang có tác vụ chờ/tải lên/hoàn thiện.

Quyền `youtube` được dùng vì ứng dụng vừa tải video vừa quản lý thành viên playlist. Không yêu cầu mật khẩu Google. Không kết nối tài khoản qua trang đăng nhập do UpVideo tự dựng.

Google có thể yêu cầu audit project để video upload qua API được công khai. Đổi trạng thái OAuth sang Production không tự thay thế audit YouTube API. Refresh token External/Testing với quyền YouTube thường hết hạn sau 7 ngày. Hạn mức và trạng thái thực tế xem trong Google Cloud/YouTube Studio.

## Chuẩn bị thư mục

```text
Videos/
  video-01.mp4
  video-01.txt
  video-01.jpg
  video-02.mov
  video-02.docx
  video-02.png
```

- Quét đệ quy: MP4, MOV, MKV, AVI, WEBM, M4V. Không nhận MP3 vì chưa có chức năng tạo video từ âm thanh.
- Mỗi video là một công việc độc lập, kể cả khi nhiều video nằm chung thư mục.
- Ưu tiên TXT/DOCX và ảnh cùng tên video. Với thư mục chỉ có một video, chấp nhận `info.txt`/`info.docx`, hoặc một file nội dung duy nhất và một ảnh duy nhất.
- Không lấy ngẫu nhiên file TXT đầu tiên cho nhiều video. Video thiếu nội dung được cảnh báo và dùng tên file làm tiêu đề.
- Nhận diện trùng bằng SHA-256 toàn bộ nội dung file. Lần quét đầu của file lớn có thể lâu; thao tác chạy nền. File gốc không bị sửa, đổi tên hoặc xóa.
- `done.json` của chương trình cũ không phải bằng chứng video đã đăng trên kênh hiện tại và không được nhập làm lịch sử. Khi chuyển từ tool cũ, chỉ chọn video chưa đăng hoặc tự kiểm tra kênh trước.

### Một video với nội dung riêng cho nhiều kênh

Trong **Thêm video vào hàng đợi**, chọn **Nhiều kênh, nội dung riêng từng kênh**, chọn thư mục gốc và tick các kênh theo thứ tự muốn gán. Mỗi thư mục con trực tiếp của thư mục gốc là một video, chứa đúng một file video và một thư mục con cho mỗi kênh đã chọn. Mỗi thư mục nội dung cần một TXT/DOCX có trường `Title:` và một ảnh JPG/PNG. Các thư mục nội dung được sắp xếp tự nhiên theo tên rồi gán lần lượt theo thứ tự tick kênh. Ứng dụng hiện bản xem trước tên video, kênh, tiêu đề và thumbnail trước khi tạo bản nháp.

```text
Batch/
  Video A/
    source.mp4
    edit-1/       # kênh được tick thứ nhất
      info.txt
      thumb.png
    edit-2/       # kênh được tick thứ hai
      info.txt
      thumb.png
  Video B/
    source.mov
    edit-1/
      info.docx
      cover.jpg
    edit-2/
      info.txt
      thumbnail.png
```

Mỗi cặp video/kênh thành một bản nháp riêng. Folder nội dung cần đủ theo số kênh đã tick. Luồng nhập này không bật theo dõi thư mục tự động; muốn nhập thay đổi mới, hãy chạy lại thao tác và kiểm tra bản xem trước.

Mẫu TXT:

```text
Title:
Tiêu đề video

Video Description:
Mô tả nhiều dòng.
Liên kết và hashtag.

Tags:
từ khóa 1, từ khóa 2
```

Hỗ trợ tiêu đề trường bằng tiếng Việt (`Tiêu đề:`, `Giới thiệu:`, `Thẻ tag video:`), UTF-8 BOM, xuống dòng và loại tag trùng. Trường chỉ được nhận diện từ đầu dòng. DOCX chỉ đọc phần văn bản chính, không nhận nội dung trong ảnh hoặc chuyển kiểu định dạng Word.

## Quy trình sử dụng

1. **Thêm video:** chọn kênh và thư mục. Chương trình tạo bản nháp, chưa upload.
2. **Sửa nội dung:** bấm tên video. Kiểm tra tiêu đề, mô tả, tag, danh mục, ngôn ngữ, thumbnail và playlist. Playlist được nhận diện bằng ID và tải đủ các trang kết quả.
3. **Khai báo nội dung:** chọn Có/Không cho video dành cho trẻ em và nội dung chỉnh sửa/tổng hợp cần khai báo; chọn thêm **Ngôn ngữ (Language)** và **Danh mục (Category)** cho nhiều bản nháp cùng lúc. Nếu các video có giá trị khác nhau, ngôn ngữ/danh mục mặc định là **Giữ nguyên từng video**. Chọn **Không khai báo** để bỏ ngôn ngữ đã lưu.
4. **Hiển thị và xếp lịch:** tại cột **Lịch / Hiển thị**, mỗi video có thể chọn **Theo thiết lập kênh**, **Riêng tư**, **Không công khai**, **Công khai ngay** hoặc **Lên lịch công khai**. Thiết lập riêng của video có ưu tiên cao nhất. Nút **Xếp lịch** cấu hình mặc định của kênh; chế độ Lên lịch dùng ngày bắt đầu, khung giờ và khoảng cách ngày. `Mỗi 1 ngày` là hằng ngày, `Mỗi 2 ngày` là cách ngày. YouTube Data API không có thuộc tính tạo Premiere, nên Công chiếu phải được bật trực tiếp trong YouTube Studio.
5. Mặc định đồng bộ lịch trên YouTube, kết hợp với lịch lưu trong hàng đợi và nối tiếp sau lịch xa nhất. Việc đồng bộ duyệt playlist uploads đầy đủ và dùng quota; kênh có nhiều video có thể cần thời gian. Nếu không đọc được API, thao tác báo lỗi thay vì âm thầm giả định kênh trống. Có thể chủ động bỏ chọn đồng bộ để chỉ dựa vào dữ liệu cục bộ.
6. **Bắt đầu:** xem lại kênh/chế độ hiển thị rồi bấm xác nhận tải lên. Tối đa 10 kênh chạy đồng thời, mỗi kênh chỉ có một worker kể cả khi kết nối qua nhiều OAuth client.
7. **Tạm dừng:** dừng ở ranh giới phần dữ liệu, không hủy phần mạng đang gửi. Tiếp tục truy vấn vị trí upload do Google xác nhận.

## Khôi phục và trạng thái

- Giao diện ghi nhớ video đang chọn, bộ lọc, kênh, tìm kiếm và mục đang mở trong bộ nhớ trình duyệt. Các tab/cửa sổ cùng địa chỉ và cùng hồ sơ trình duyệt được đồng bộ; khi quay lại tab, tiến trình được lấy mới từ Python. Không đồng bộ nội dung form chưa lưu hoặc tự thực hiện lệnh upload. Hồ sơ trình duyệt khác, chế độ riêng tư và địa chỉ/cổng khác không dùng chung trạng thái giao diện.

- SQLite sử dụng WAL và `synchronous=FULL`. Công việc đang chạy khi ứng dụng khởi động lại được chuyển sang Tạm dừng; không tự xuất bản ngay lúc mở lại.
- Lưu URL phiên resumable upload, vị trí byte và video ID. Khi mất phản hồi của phần cuối, truy vấn lại phiên trước khi gửi tiếp.
- Video ID được ghi ngay sau khi Google xác nhận. Thumbnail/playlist lỗi sẽ báo **Cần hoàn thiện**; thử lại không upload video đó lần nữa.
- Thêm playlist kiểm tra thành viên đã có trước khi gửi lệnh thêm, tránh lặp lại sau mất phản hồi.
- Lỗi mạng/429/5xx khi gửi các phần video có backoff lũy tiến và jitter. Sau giới hạn thử lại, người dùng chủ động tiếp tục. Một số thao tác metadata/đăng nhập cần bấm thử lại.
- Quota lỗi sẽ tạm dừng những công việc còn chờ thuộc project/kênh liên quan. Các yêu cầu đang chạy trên worker khác có thể vẫn kết thúc yêu cầu hiện tại.
- Phiên Google hết hạn (404/410) không tự tạo mới: cần kiểm tra YouTube Studio và xác nhận video chưa tồn tại rồi bấm **Tạo lại phiên upload**. Không thể đảm bảo “đúng một lần” tuyệt đối khi phía Google mất thông tin phiên.
- Nếu lịch của phiên chưa hoàn tất đã qua, chương trình chặn gửi tiếp phần video để tránh công khai ngoài ý muốn. Kiểm tra kênh rồi tạo lại phiên với lịch mới.
- **Đã tải lên** nghĩa là đã có video ID, hoàn tất các bước phụ và đã đọc lại trạng thái; không khẳng định YouTube đã xử lý video xong hoặc đã phát công khai. Bấm **Mở video** để kiểm tra.
- Không sửa metadata của phiên đang chạy. Bản nháp có thể sửa; video đã upload cần chỉnh trong YouTube Studio. Không tự xóa video trên YouTube.

## Bảo vệ dữ liệu

Máy chủ cục bộ kiểm tra Host, Origin và token yêu cầu để hạn chế truy cập từ website khác. Giao diện không trả token Google hoặc URL phiên upload. Các trang có CSP, không tải CDN hoặc font bên ngoài. Nhật ký không ghi authorization code/token; dữ liệu xuất hiện trong giao diện được escape.

Database chứa đường dẫn, nội dung video, lịch sử và URL phiên upload. Hãy bảo vệ tài khoản Windows và bản sao lưu của thư mục dữ liệu. Khi ngắt kết nối, token cục bộ được xóa; để thu hồi grant Google, vào trang quản lý ứng dụng được cấp quyền của tài khoản Google.

## Cấu trúc mã nguồn

```text
run.py                 khởi động, khóa một phiên ứng dụng, mở trình duyệt
studio/domain.py       quét file, đọc TXT/DOCX, validation, tính lịch
studio/store.py        SQLite và nhật ký
studio/vault.py        mã hóa Windows DPAPI
studio/google.py       OAuth PKCE, refresh token, YouTube REST API
studio/engine.py       hàng đợi, upload resumable, retry, bước hoàn thiện
studio/server.py       API cục bộ, bảo vệ yêu cầu, tác vụ nền
studio/picker.py       hộp thoại chọn thư mục của Windows
web/                   HTML/CSS/JavaScript giao diện
tests/test_studio.py   kiểm thử miền nghiệp vụ, upload giả lập, HTTP cục bộ
```

## Kiểm thử và đóng gói

```powershell
python -m unittest discover -s tests -v
python -m compileall -q studio run.py
node --check web/app.js
node --test tests/test_http.cjs tests/test_ui_state.cjs tests/test_selection.cjs
```

Node chỉ cần cho kiểm tra JavaScript, không cần để chạy ứng dụng. Test DPAPI phải chạy dưới tài khoản Windows thông thường; tài khoản sandbox hạn chế có thể không được dùng DPAPI. Các test upload dùng Google giả lập, không đăng video thật.

Đóng gói Windows (PyInstaller là dependency dành cho bước build):

```powershell
python -m pip install -r requirements-build.txt
.\Build.cmd
```

EXE độc lập được tạo tại `dist/UpVideoStudio.exe`. `Build.cmd` đóng gói chế độ `--onefile --windowed`; có thể gửi riêng file EXE này, không cần kèm thư mục `_internal` hoặc dự án. Bản build chưa ký số nên Windows SmartScreen vẫn có thể cảnh báo.

## Trạng thái kiểm chứng

Đã kiểm tra cú pháp Python/JavaScript và bộ test tự động cục bộ. Đợt audit này không thực hiện đăng nhập hoặc upload thật; kiểm thử upload dùng Google giả lập. Chưa kiểm tra trực quan trong Browser vì phiên làm việc không có trình duyệt kết nối.

## Tài liệu giao thức

- [YouTube resumable uploads](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [OAuth cho Desktop apps và PKCE](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Video metadata](https://developers.google.com/youtube/v3/docs/videos)
- [Playlist items](https://developers.google.com/youtube/v3/docs/playlistItems/insert)
- [Hạn mức API](https://developers.google.com/youtube/v3/determine_quota_cost)
- [Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)

Chạy `Build.cmd` để tạo bản chạy độc lập tại `dist/UpVideoStudio.exe`. Chỉ phân phối file EXE mới nhất; không lưu nhiều bản build trong workspace.
