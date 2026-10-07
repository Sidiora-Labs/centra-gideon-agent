# Ollama models

Bundled chat, embeddings and model management through a configured Ollama server. The [manifest](app.json) declares the endpoint, chat model, embedding model, optional context capacity and request timeout. The default endpoint is `http://localhost:11434`.

Installing the bundle does not install or start Ollama and does not download model weights. Inference requires a reachable server and an available model. Model pulls are explicit requests through the server's API.

A loopback endpoint is not proof that every selected model executes locally. The provider reports per-model remote execution when server records name a remote host or the model carries the recognized cloud tag. Inspect the actual model discovery and execution-locality result before relying on local-only behavior.

Chat and embedding capabilities come from the model the server reports it serves. Missing usage or capacity measurements remain unknown unless an explicit supported override is configured. Successful discovery does not by itself qualify inference, embeddings or deletion.
