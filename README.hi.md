# Gideon Agent

यह रिपॉज़िटरी ओपन-सोर्स सेल्फ-होस्टेड संस्करण का दस्तावेज़ है। Gideon एक होस्टेड सेवा भी प्रदान करता है।

Gideon एक व्यक्तिगत एजेंट है जिसमें वेब कंसोल, चैट, कार्य, स्वचालन, ज्ञान और अनुमति-आधारित ऐप शामिल हैं।

यह संक्षिप्त स्थापना मार्गदर्शिका है। पूरी और वर्तमान जानकारी के लिए अंग्रेज़ी README और नीचे दिए गए दस्तावेज़ देखें।

## शुरुआत

सोर्स से बिल्ड करने के लिए Python 3.12+, Node.js 22.12+ और Rust/Cargo 1.91.1 चाहिए। प्लेटफ़ॉर्म wheel में Hypermid daemon पहले से होता है। गेटवे अतिरिक्त प्रोसेस शुरू कर सकता है; सब कुछ एक ही प्रोसेस में नहीं चलता।

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

पहली शुरुआत के बाद प्रोवाइडर कॉन्फ़िगर करें। Hypermid मेमोरी वैकल्पिक है और उसे कॉन्फ़िगर करना होता है। वर्तमान अनुमतियाँ और स्वीकृतियाँ टूल सीमित करती हैं। अपडेट जाँच और इंटीग्रेशन बाहरी अनुरोध कर सकते हैं।

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
