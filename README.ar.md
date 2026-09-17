<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon هو وكيل ذكاء اصطناعي شخصي يعمل على جهازك الخاص. تُقدِّم عملية واحدة وحدة تحكم ويب وتؤدي العمل: الدردشة، وحلقات الأهداف الطويلة الأمد، والذاكرة، وقاعدة المعرفة، والمهام، والجداول الزمنية، وصندوق وارد، ومنصة تطبيقات محكومة بالأذونات.

وهو مصمَّم لشخص واحد يريد وكيلًا يمتلك وصولًا حقيقيًا إلى حاسوبه الخاص وخدماته الخاصة، دون تسليم المفاتيح لمنتج مستضاف. تعيش الحالة في دليل تختاره أنت. ومزوِّدو النماذج قابلون للتوصيل: مفتاح Anthropic أو OpenAI، أو نقطة نهاية متوافقة مع OpenAI، أو بيانات اعتماد AWS Bedrock، أو نموذج يعمل محليًا.

> **قبل الإصدار 1.0:** Gideon في الإصدار **v0.1.3**. فهو يتطور بسرعة، وقد يُفسد أحد الإصدارات شيئًا ما. شغِّل `gideon snapshot` قبل الترقية، واقرأ [CHANGELOG.md](CHANGELOG.md) لمعرفة ما تم إصداره.

## ما الذي يفعله

- **تحدَّث معه، أو سلِّمه العمل.** جلسات دردشة مع ردود متدفقة، واستدعاءات للأدوات، ومخرجات، ونص محادثة يمكنك البحث فيه. يتولى الوكلاء الفرعيون مهمة ويقدمون تقريرًا بينما تواصل أنت عملك.
- **دعه يعمل دون إشراف.** تعمل الحلقات على هدف ما عبر جولات عديدة وفق جدول زمني. وتحوِّل المهام والمشغِّلات وسير العمل الطلبات العابرة إلى شيء قابل للتكرار.
- **امنحه ذاكرة.** تحفظ الذاكرة متعددة الطبقات التفضيلات والسياق بين المحادثات. وتحتوي قاعدة المعرفة على المستندات التي توجهه إليها، فتستشهد الإجابات بموادك بدلًا من الإنترنت المفتوح.
- **ابقَ على اطلاع.** يجمع صندوق الوارد ما يحتاج إليك، من قنوات مثل Slack وكذلك من Gideon نفسه. والإدخال الصوتي والردود المنطوقة إضافات اختيارية.
- **وسِّعه.** تغطي منصة التطبيقات وحزمة تطوير Python الخاصة بها (`gideon.sdk`) النماذج والقنوات والبحث والأدوات ولوحات المعلومات. وتضيف المهارات والموجِّهات وخوادم MCP قدرات دون المساس بالنواة.
- **حدِّد ما يجوز له لمسه.** موافقات الأدوات، وأذونات كل تطبيق، والتعامل مع بيانات الاعتماد، وفحص الأوامر، وسجل تدقيق. النواة محايدة تجاه المزوِّدين: تعيش التكاملات في التطبيقات، وليس أبدًا في حزمة النواة.
- **راقبه وهو يعمل.** تعرض وحدة التحكم الجلسات والنشاط والحلقات الجارية والمهام المجدولة والحالة الصحية، بالإضافة إلى طرفية للجهاز الذي يعمل عليه Gideon.

## المتطلبات

- Python 3.12 أو أحدث.
- Node.js 22.12 أو أحدث مع npm، إن أردت بناء وحدة التحكم من المصدر. تبني CI وحدة التحكم باستخدام Node 24.
- macOS أو Linux. على Windows، استخدم مسار Docker Compose في [docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md).
- مزوِّد نموذج لأي شيء يعتمد على نموذج، يُضبط بعد أول تشغيل. ويعمل النموذج المحلي أيضًا.

لا توجد قاعدة بيانات خارجية ولا وسيط رسائل. فكل شيء يعمل داخل عملية البوابة الواحدة.

## التشغيل من نسخة محلية من المستودع

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

افتح عنوان وحدة التحكم الذي تطبعه البوابة. يكتب `npm run build` وحدة التحكم إلى `apps/console/dist`، وتقدمها البوابة مباشرة من النسخة المحلية. ويسألك `gideon setup` عن دليل مساحة العمل والمنطقة الزمنية. أما مزوِّدو النماذج فيُضبطون بعد ذلك، في وحدة التحكم، لأن المنزل الجديد لا يحتوي بعد على تطبيق مزوِّد يحمل بيانات اعتماد.

`GIDEON_HOME` هو الدليل الذي يحتفظ بالإعدادات وبيانات الاعتماد والمحادثات وغيرها من حالات التشغيل. ومن دونه، يستخدم Gideon المسار `~/.gideon`. والاحتفاظ بقيمة `.dev-home` المعزولة أثناء التجربة أمر مقصود: فهو يُبقي نسخة التطوير بعيدًا عن نسختك الحقيقية. أوقف البوابة العاملة في المقدمة بـ Ctrl-C.

للحصول على تثبيت معبَّأ بدلًا من ذلك، يقوم `sh infrastructure/website/install.sh` بالتهيئة باستخدام `uv`. وللتحقق من بايتات ذلك المُثبِّت قبل تشغيله، انظر [التحقق من سطر الأوامر الواحد](docs/guides/GETTING_STARTED.md#verify-the-one-liner). والدليل الكامل، من عدم وجود أي شيء مثبَّتًا إلى أول دردشة، موجود في [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md).

## التطوير

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

يقوم ملف الخطاف بتنسيق ملفات Python المُجهَّزة وتوقيع التزاماتك، وهو ما تتحقق منه CI. ولا يثبِّت `npm install` الخطافات.

| الأمر | ما الذي يفعله |
| --- | --- |
| `make format` | تنسيق Python باستخدام black و isort |
| `make lint` | black و isort و flake8 و mypy على وقت التشغيل والفحوصات |
| `make test` | تشغيل حزمة اختبارات Python (`checks/runtime`) |
| `make serve` | بناء وحدة التحكم وتشغيل بوابة مقابل `.dev-home` |
| `make serve-web` | تشغيل خادم تطوير وحدة التحكم على المنفذ 3100 مقابل بوابة |
| `npm run typecheck:web` | فحص أنواع وحدة التحكم |
| `npm run test:web` | اختبارات وحدة التحكم باستخدام Vitest |
| `make test-e2e` | فحوصات التفاعل في Chromium |
| `make docker-up` | تشغيل مجموعة الحاويات من `infrastructure/compose` |

يغطي [CONTRIBUTING.md](CONTRIBUTING.md) اتفاقية العمل، وتوقيع DCO، وكيفية تصنيف التغييرات ومراجعتها.

## خريطة المستودع

| المسار | المحتويات |
| --- | --- |
| `runtime/gideon/core` | الإعدادات والموارد المشتركة ومساعدات الاستمرارية |
| `runtime/gideon/engine` | تنفيذ الوكيل وتنسيق وقت التشغيل |
| `runtime/gideon/cognition` | الذاكرة والمعرفة وتجميع السياق |
| `runtime/gideon/automation` | الجداول الزمنية والمشغِّلات وسير العمل |
| `runtime/gideon/security` | الأذونات والتعامل مع بيانات الاعتماد والفحص |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | تكامل المزوِّدين والتطبيقات |
| `runtime/gideon/interfaces` | واجهات CLI والبوابة وواجهة برمجة تطبيقات وحدة التحكم |
| `runtime/gideon/sdk` | حزمة تطوير التطبيقات، تُستورد باسم `gideon.sdk` |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | التحديث الذاتي والنسخ الاحتياطي والتحقق |
| `apps/console` | وحدة تحكم React وأصول العميل المشتركة |
| `apps/desktop`, `apps/mobile` | هيكلا Electron و Capacitor |
| `packages/python-client` | عميل Python لواجهة برمجة تطبيقات البوابة |
| `checks/runtime`, `checks/harness` | فحوصات السلوك وأداة التطوير الذاتي |
| `docs` | البنية والمعاينات والمرجع والأمان والتصميم |
| `tooling`, `infrastructure` | نصوص التطوير والتعبئة والحاويات والموقع |
| `examples` | قالب تطبيق ومثال سجل، لا يُقدَّم أي منهما ولا يُثبَّت |

## التوثيق

[docs/README.md](docs/README.md) هو الفهرس. والمسار المختصر للداخل:

- [docs/VISION.md](docs/VISION.md) لمعرفة ما يسعى هذا المشروع إلى أن يكونه.
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) لمعرفة كيفية تجميع البوابة.
- [docs/reference/CLI.md](docs/reference/CLI.md) لكل أمر وعلم (flag).
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md) لكل إعداد.
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md) لمعرفة حدود الثقة الفعلية.

## الحالة

Gideon في مرحلة ما قبل 1.0 وتحت تطوير نشط. البوابة، ووحدة التحكم، وأصداف سطح المكتب والهاتف، وعميل Python، والفحوصات التي تمرِّنها، كلها موجودة في الشجرة، وتُشغِّل CI حزمة اختبارات Python واختبارات وحدة التحكم وفحوصات التفاعل عند كل تغيير.

وما لا يعنيه ذلك: لا توجد خدمة مستضافة، ولا يفترض هذا المستودع وجود حزمة منشورة ولا نقطة نهاية إصدار ما لم توجِّه `GIDEON_RELEASE_REPOSITORY` إلى إحداها. وتحتاج التكاملات إلى إعداداتها وبيانات اعتمادها ودعم منصتها الخاص. وبعض القدرات في الشجرة لم تُجرَّب من البداية إلى النهاية مقابل مزوِّد حقيقي. والفحوصات الناجحة تقول شيئًا عن نطاق تغطيتها ولا تقول شيئًا عن الباقي.

## الأمان

يقرأ Gideon ملفات محلية، ويشغِّل أدوات، ويتحدث إلى خدمات تضبطها أنت، لذا فإن رمز البوابة والحساب الذي تعمل تحته يمنحان وصولًا حقيقيًا. توجَّه الإبلاغات عبر قناة خاصة إلى من أعطاك النسخة المحلية. انظر [SECURITY.md](SECURITY.md).

لا يرسل Gideon أي قياسات عن بعد. ولا شيء عن استخدامك يغادر جهازك إلا إذا ضبطت تكاملًا يفعل ذلك.

## المساهمة والحصول على المساعدة

- [CONTRIBUTING.md](CONTRIBUTING.md) للإعداد والأوامر وتوقيع DCO والمراجعة.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) لكيفية تعاملنا مع بعضنا هنا.
- [SUPPORT.md](SUPPORT.md) لأماكن السؤال، وما يجب تضمينه عند السؤال.
- [GOVERNANCE.md](GOVERNANCE.md) لمن يقرر ماذا.
- [القضايا](https://github.com/Sidiora-Labs/centra-gideon-agent/issues) للأخطاء والأفكار، و[الإصدارات](https://github.com/Sidiora-Labs/centra-gideon-agent/releases) لما تم إصداره، و[سياسة الأمان](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy) لكيفية التعامل مع الثغرات الأمنية. أما المستودع نفسه فيوجد في [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent).

## الترخيص

Apache License 2.0. انظر [LICENSE](LICENSE). حقوق النشر 2026 Sidiora Labs Inc.
