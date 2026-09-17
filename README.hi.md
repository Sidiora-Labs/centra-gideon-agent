<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon एक व्यक्तिगत AI एजेंट है जो आपकी अपनी मशीन पर चलता है। एक ही प्रोसेस वेब कंसोल परोसती है और काम भी करती है: चैट, लंबे समय तक चलने वाले लक्ष्य लूप, मेमोरी, एक ज्ञान आधार (knowledge base), कार्य, शेड्यूल, एक इनबॉक्स, और अनुमति-नियंत्रित (permission-gated) ऐप प्लेटफ़ॉर्म।

यह ऐसे एक व्यक्ति के लिए बनाया गया है जो चाहता है कि उसके एजेंट को उसके ही कंप्यूटर और उसकी ही सेवाओं तक वास्तविक पहुँच हो, बिना किसी होस्टेड उत्पाद को चाबियाँ सौंपे। स्थिति (state) आपके चुने हुए निर्देशिका में रहती है। मॉडल प्रोवाइडर बदले जा सकते हैं (pluggable): एक Anthropic या OpenAI कुंजी, एक OpenAI-संगत एंडपॉइंट, AWS Bedrock क्रेडेंशियल, या स्थानीय रूप से चल रहा कोई मॉडल।

> **Pre-1.0:** Gideon **v0.1.3** पर है। यह तेज़ी से बदलता है और कोई रिलीज़ कुछ तोड़ सकती है। अपग्रेड करने से पहले `gideon snapshot` चलाएँ, और यह पढ़ें कि क्या जारी हुआ — [CHANGELOG.md](CHANGELOG.md)।

## यह क्या करता है

- **इससे बात करें, या काम सौंप दें।** स्ट्रीमिंग उत्तरों, टूल कॉल, आर्टिफ़ैक्ट और खोजे जा सकने वाले ट्रांसक्रिप्ट के साथ चैट सत्र। सबएजेंट कोई काम ले लेते हैं और आपके काम करते रहते हुए वापस रिपोर्ट करते हैं।
- **इसे बिना निगरानी चलने दें।** लूप किसी लक्ष्य पर कई टर्न तक एक शेड्यूल पर काम करते हैं। कार्य, ट्रिगर और वर्कफ़्लो एकबारगी अनुरोधों को दोहराने योग्य बना देते हैं।
- **इसे मेमोरी दें।** स्तरित (layered) मेमोरी बातचीतों के बीच प्राथमिकताएँ और संदर्भ बनाए रखती है। ज्ञान आधार उन दस्तावेज़ों को रखता है जिनकी ओर आप इसे इंगित करते हैं, ताकि उत्तर खुले इंटरनेट के बजाय आपकी सामग्री का हवाला दें।
- **जानकारी में बने रहें।** एक इनबॉक्स वह सब इकट्ठा करता है जिसके लिए आपकी आवश्यकता है — Slack जैसे चैनलों से और Gideon से भी। वॉइस इनपुट और बोले गए उत्तर वैकल्पिक अतिरिक्त सुविधाएँ हैं।
- **इसे बढ़ाएँ।** ऐप प्लेटफ़ॉर्म और उसका Python SDK (`gideon.sdk`) मॉडल, चैनल, खोज, टूल और डैशबोर्ड को कवर करते हैं। स्किल, प्रॉम्प्ट और MCP सर्वर कोर को छुए बिना क्षमता जोड़ते हैं।
- **तय करें कि यह किस चीज़ को छू सकता है।** टूल अनुमतियाँ (approvals), प्रति-ऐप अनुमतियाँ, क्रेडेंशियल हैंडलिंग, कमांड स्क्रीनिंग और एक ऑडिट ट्रेल। कोर प्रोवाइडर-अज्ञेय (provider-agnostic) है: इंटीग्रेशन ऐप्स में रहते हैं, कोर पैकेज में कभी नहीं।
- **इसे काम करते देखें।** कंसोल सत्र, गतिविधि, चल रहे लूप, शेड्यूल किए गए जॉब और हेल्थ दिखाता है, और साथ ही उस मशीन के लिए एक टर्मिनल जिस पर Gideon काम कर रहा है।

## आवश्यकताएँ

- Python 3.12 या नया।
- Node.js 22.12 या नया, npm के साथ, यदि आप कंसोल को सोर्स से बनाना चाहते हैं। CI कंसोल को Node 24 से बनाता है।
- macOS या Linux। Windows पर, [docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md) में दिए Docker Compose मार्ग का उपयोग करें।
- किसी भी मॉडल-आधारित चीज़ के लिए एक मॉडल प्रोवाइडर, जिसे पहले स्टार्ट के बाद कॉन्फ़िगर किया जाता है। कोई स्थानीय मॉडल भी काम करता है।

कोई बाहरी डेटाबेस नहीं है और कोई मैसेज ब्रोकर नहीं है। सब कुछ उसी एक गेटवे प्रोसेस में चलता है।

## चेकआउट से चलाएँ

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

वह कंसोल पता खोलें जो गेटवे प्रिंट करता है। `npm run build` कंसोल को `apps/console/dist` में लिखता है, जिसे गेटवे सीधे चेकआउट से परोसता है। `gideon setup` एक वर्कस्पेस निर्देशिका और एक टाइमज़ोन माँगता है। मॉडल प्रोवाइडर बाद में कॉन्फ़िगर किए जाते हैं, कंसोल में, क्योंकि एक ताज़ा होम में अभी कोई प्रोवाइडर ऐप नहीं होता जो क्रेडेंशियल रख सके।

`GIDEON_HOME` वह निर्देशिका है जिसमें कॉन्फ़िगरेशन, क्रेडेंशियल, बातचीत और अन्य रनटाइम स्थिति रहती है। इसके बिना, Gideon `~/.gideon` का उपयोग करता है। प्रयोग करते समय पृथक `.dev-home` मान बनाए रखना जानबूझकर है: यह विकास इंस्टेंस को आपके असली इंस्टेंस से अलग रखता है। फ़ोरग्राउंड गेटवे को Ctrl-C से रोकें।

इसके बजाय पैकेज्ड इंस्टॉल के लिए, `sh infrastructure/website/install.sh` `uv` के साथ बूटस्ट्रैप करता है। उन इंस्टॉलर बाइट्स को चलाने से पहले जाँचने के लिए, [एक-लाइनर सत्यापित करें](docs/guides/GETTING_STARTED.md#verify-the-one-liner) देखें। पूरा वॉकथ्रू, कुछ भी इंस्टॉल न होने से लेकर पहली चैट तक, [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md) में है।

## विकास करें

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

हुक स्क्रिप्ट स्टेज किए गए Python को फ़ॉर्मैट करती है और आपके कमिट पर साइन-ऑफ़ करती है, और यही CI जाँचता है। `npm install` हुक इंस्टॉल नहीं करता।

| कमांड | यह क्या करता है |
| --- | --- |
| `make format` | black और isort से Python फ़ॉर्मैट करें |
| `make lint` | रनटाइम और चेक्स पर black, isort, flake8 और mypy |
| `make test` | Python सूट चलाएँ (`checks/runtime`) |
| `make serve` | कंसोल बनाएँ और `.dev-home` के विरुद्ध एक गेटवे शुरू करें |
| `make serve-web` | कंसोल डेव सर्वर को पोर्ट 3100 पर एक गेटवे के विरुद्ध चलाएँ |
| `npm run typecheck:web` | कंसोल का टाइप-चेक करें |
| `npm run test:web` | Vitest के साथ कंसोल टेस्ट |
| `make test-e2e` | Chromium इंटरैक्शन चेक |
| `make docker-up` | `infrastructure/compose` से कंटेनर स्टैक शुरू करें |

[CONTRIBUTING.md](CONTRIBUTING.md) कार्य-समझौते, DCO साइन-ऑफ़, और परिवर्तनों को कैसे वर्गीकृत और समीक्षित किया जाता है, इन सबको कवर करता है।

## रिपॉज़िटरी मानचित्र

| पथ | सामग्री |
| --- | --- |
| `runtime/gideon/core` | कॉन्फ़िगरेशन, साझा संसाधन, पर्सिस्टेंस हेल्पर |
| `runtime/gideon/engine` | एजेंट निष्पादन और रनटाइम समन्वय |
| `runtime/gideon/cognition` | मेमोरी, ज्ञान और संदर्भ संयोजन |
| `runtime/gideon/automation` | शेड्यूल, ट्रिगर और वर्कफ़्लो |
| `runtime/gideon/security` | अनुमतियाँ, क्रेडेंशियल हैंडलिंग और स्क्रीनिंग |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | प्रोवाइडर और अनुप्रयोग इंटीग्रेशन |
| `runtime/gideon/interfaces` | CLI, गेटवे और कंसोल API सतहें |
| `runtime/gideon/sdk` | ऐप SDK, जिसे `gideon.sdk` के रूप में इम्पोर्ट किया जाता है |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | सेल्फ-अपडेट, बैकअप और सत्यापन |
| `apps/console` | React कंसोल और साझा क्लाइंट एसेट्स |
| `apps/desktop`, `apps/mobile` | Electron और Capacitor शेल |
| `packages/python-client` | गेटवे API के लिए Python क्लाइंट |
| `checks/runtime`, `checks/harness` | व्यवहार चेक और स्व-विकास हार्नेस |
| `docs` | आर्किटेक्चर, गाइड, संदर्भ, सुरक्षा और डिज़ाइन |
| `tooling`, `infrastructure` | विकास स्क्रिप्ट, पैकेजिंग, कंटेनर, वेबसाइट |
| `examples` | एक ऐप टेम्पलेट और एक रजिस्ट्री उदाहरण, जिन्हें न परोसा जाता है न इंस्टॉल किया जाता है |

## दस्तावेज़ीकरण

[docs/README.md](docs/README.md) सूचकांक है। इसमें छोटा रास्ता:

- [docs/VISION.md](docs/VISION.md) यह जानने के लिए कि यह क्या बनना चाहता है।
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) यह जानने के लिए कि गेटवे कैसे जोड़ा गया है।
- [docs/reference/CLI.md](docs/reference/CLI.md) हर कमांड और फ़्लैग के लिए।
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md) हर सेटिंग के लिए।
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md) यह जानने के लिए कि विश्वास की सीमाएँ (trust boundaries) वास्तव में क्या हैं।

## स्थिति

Gideon pre-1.0 है और सक्रिय विकास में है। गेटवे, कंसोल, डेस्कटॉप और मोबाइल शेल, Python क्लाइंट, और उन्हें चलाने वाले चेक — सब ट्री में हैं, और CI हर परिवर्तन पर Python सूट, कंसोल टेस्ट और इंटरैक्शन चेक चलाता है।

इसका यह अर्थ नहीं है: कोई होस्टेड सेवा मौजूद नहीं है, और यह रिपॉज़िटरी न तो किसी प्रकाशित पैकेज को और न ही किसी रिलीज़ एंडपॉइंट को मानती है, जब तक कि आप `GIDEON_RELEASE_REPOSITORY` को किसी एक की ओर इंगित न करें। इंटीग्रेशन को अपना कॉन्फ़िगरेशन, क्रेडेंशियल और प्लेटफ़ॉर्म समर्थन चाहिए। ट्री में कुछ क्षमताएँ किसी लाइव प्रोवाइडर के विरुद्ध एंड-टू-एंड नहीं चलाई गई हैं। पास होते चेक उस बारे में कुछ कहते हैं जिसे वे कवर करते हैं, और बाकी सब के बारे में कुछ नहीं।

## सुरक्षा

Gideon स्थानीय फ़ाइलें पढ़ता है, टूल चलाता है और आपके कॉन्फ़िगर किए गए सेवाओं से बात करता है, इसलिए गेटवे टोकन और जिस खाते के अधीन यह चलता है, वे वास्तविक पहुँच देते हैं। रिपोर्टें एक निजी चैनल के ज़रिए उस व्यक्ति तक जाती हैं जिसने आपको चेकआउट दिया। [SECURITY.md](SECURITY.md) देखें।

Gideon कोई टेलीमेट्री नहीं भेजता। आपके उपयोग के बारे में कुछ भी आपकी मशीन से बाहर नहीं जाता, जब तक कि आप ऐसा करने वाला कोई इंटीग्रेशन कॉन्फ़िगर न करें।

## योगदान और सहायता पाना

- [CONTRIBUTING.md](CONTRIBUTING.md) सेटअप, कमांड, DCO साइन-ऑफ़ और समीक्षा के लिए।
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) यह जानने के लिए कि हम यहाँ एक-दूसरे के साथ कैसा व्यवहार करते हैं।
- [SUPPORT.md](SUPPORT.md) यह जानने के लिए कि कहाँ पूछें, और पूछते समय क्या शामिल करें।
- [GOVERNANCE.md](GOVERNANCE.md) यह जानने के लिए कि कौन क्या तय करता है।
- बग और विचारों के लिए [इश्यू](https://github.com/Sidiora-Labs/centra-gideon-agent/issues), क्या जारी हुआ यह जानने के लिए [रिलीज़](https://github.com/Sidiora-Labs/centra-gideon-agent/releases), और भेद्यताओं को कैसे संभाला जाता है यह जानने के लिए [सुरक्षा नीति](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy)। रिपॉज़िटरी स्वयं [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent) पर रहती है।

## लाइसेंस

Apache License 2.0। [LICENSE](LICENSE) देखें। Copyright 2026 Sidiora Labs Inc.