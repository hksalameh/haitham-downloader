# Haitham Downloader Classic 4.1 (Windows)

مدير تحميل عربي/إنجليزي بواجهة كلاسيكية، مع استكمال التحميل وتقسيم الملفات الداعمة لـ HTTP Range وإضافة اختيارية لمتصفح Chrome.

**الحالة:** إصدار تجريبي (Preview). لم تثبت مساواته الكاملة لبرنامج IDM، ولا يُنصح بإزالة مدير التحميل القديم حتى الاختبار.

## طريقة الاستخدام على ويندوز

1. من **Releases** حمّل أحدث ملف `HaithamDownloader_Classic_420.exe`.
2. تحتاج إلى Windows 10/11 و WebView2 Runtime، ووجود `aria2c.exe` في PATH. يمكن تثبيت aria2 باستخدام `winget install --id aria2.aria2 -e`.
3. شغّل EXE. لا يلزم تثبيت Python عند تشغيل EXE.
4. لتفعيل Chrome: افتح `chrome://extensions`، فعّل Developer mode ثم Load unpacked واختر مجلد `browser-extension` من نسخة المشروع التي نزّلتها ZIP. راجع صلاحيات الإضافة قبل التفعيل.
5. اختر مجلد التحميل الافتراضي. في نافذة التحميل يمكنك اختيار مجلد مختلف وتحديد خيار تذكّره للمرّة القادمة. يمكن إعادة تعيينه للمجلد الافتراضي.
6. التحميل المقسّم يخزن الأجزاء المؤقتة على **نفس قرص الوجهة** افتراضيًا؛ يمكن تعيين مجلد مؤقت مستقل من الإعدادات.

البيانات والإعدادات تبقى محلية في `%LOCALAPPDATA%\HaithamDownloaderV41Classic`.

## التشغيل من المصدر

المتطلبات: Python 3.12+، aria2c في PATH، و Windows WebView2.

```powershell
python -m pip install -r requirements.txt
python app.py
```

## بناء ملف EXE

```powershell
python -m PyInstaller --noconfirm HaithamDownloader_Classic_420.spec
```

المخرج `dist/HaithamDownloader_Classic_420.exe`.

## قراءة الأداء أثناء التحميل

في وضع HTTP Range، يعرض عمود «اتصالات/أجزاء» عدد طلبات HTTP الجارية / إجمالي أجزاء الملف. مثلًا `2/12` تعني أن طلبين فقط يعملان الآن رغم وجود 12 جزءًا. عند رد الخادم HTTP 429 قد تظهر عبارة «انتظار الخادم»؛ هذا انتظار لإعادة المحاولة، وليس سرعة صفر بسبب خط الإنترنت بالضرورة. تعرض نافذة التفاصيل الحد الحالي للطلبات، ولا تعني الأجزاء المكتملة وجود اتصالات مفتوحة.

## اختبارات

```powershell
python test_range_connections.py
python test_adaptive_retry.py
python test_connection_telemetry.py
python test_missing_file.py
node --check web/ui.js
node test_chrome_single_dialog.cjs
```

لا تعني الاختبارات المحلية نجاح جميع مواقع الويب التي تتطلب cookies أو روابط موقّعة، ولا تدعم الأداة فك DRM.

## حالة macOS

شيفرة التحميل بلغة Python قابلة للتكييف، لكن نسخة Mac تحتاج بناءً واختبارًا على macOS مع اختيار بديل لـ WebView2، والتحقق من aria2، وإضافة المتصفح، وسلوك أيقونة شريط القوائم. **لا تتوفر نسخة Mac معتمدة حاليًا.**

## الخصوصية والمساهمات

التطبيق المحلي يستقبل إضافة Chrome على 127.0.0.1. لا تنشر ملفات البيانات الشخصية من LOCALAPPDATA، ولا سجلات التحميل أو ملفات cookies. ملفات build وdist والبيانات المؤقتة ليست ضمن شيفرة المصدر.
