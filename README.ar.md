# Gideon Agent

Gideon وكيل شخصي يوفر لوحة ويب ومحادثات ومهام وأتمتة ومعرفة وتطبيقات مقيدة بالأذونات.

هذه مقدمة مختصرة للتثبيت. راجع README الإنجليزي والأدلة المرتبطة للاطلاع على التفاصيل الكاملة والحالية.

## البدء

يتطلب البناء من المصدر Python 3.12+ وNode.js 22.12+ وRust/Cargo 1.91.1. تتضمن حزمة wheel الخاصة بالمنصة خدمة Hypermid. يمكن للبوابة تشغيل عمليات إضافية؛ ليست كل الوظائف ضمن عملية واحدة.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

اضبط مزود النموذج بعد التشغيل الأول. ذاكرة Hypermid اختيارية وتتطلب إعدادًا. تظل الأذونات والموافقات الحالية مقيدة للأدوات. قد ترسل فحوص التحديث والتكاملات طلبات إلى خدمات خارجية.

`GIDEON_HOME`: `.dev-home` / `~/.gideon`.

- [README (English)](README.md)
- [Setup](docs/guides/GETTING_STARTED.md)
- [Platforms](docs/guides/PLATFORMS.md)
- [Containers](docs/guides/CONTAINERS.md)
- [Desktop](docs/guides/DESKTOP.md)
- [Configuration](docs/reference/CONFIGURATION_REFERENCE.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Apache License 2.0](LICENSE)
