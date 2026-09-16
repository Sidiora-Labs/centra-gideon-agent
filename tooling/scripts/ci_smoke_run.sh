#!/usr/bin/env bash
                                                                                
#
                                                                                     
                                                                                   
                                         
#
        
                                                                             
                                                     
#
                                                                            
                                                                                  
                                                                              

set -euo pipefail

PROMPT="${PROMPT:-Reply with exactly the word: OK}"
EXPECT="${EXPECT:-OK}"

                                                                                      
                                                                                   
if [[ -z "${GIDEON_HOME:-}" ]]; then
  GIDEON_HOME="$(mktemp -d)/gideon-home"
  export GIDEON_HOME
  echo "smoke: using a throwaway GIDEON_HOME=$GIDEON_HOME" >&2
fi

fail() { echo "smoke: FAIL — $1" >&2; exit 1; }

                                                                                  
echo "smoke: [1/4] --format plain" >&2
out="$(gideon run -p "$PROMPT" --format plain)" || fail "plain run exited nonzero"
[[ "$out" == *"$EXPECT"* ]] || fail "plain output did not contain '$EXPECT' (got: $out)"

                                                                                  
echo "smoke: [2/4] --format json" >&2
doc="$(gideon run -p "$PROMPT" --format json)" || fail "json run exited nonzero"
result="$(printf '%s' "$doc" | jq -er '.result')" || fail "json output has no .result"
[[ -n "$result" ]] || fail ".result was empty"
printf '%s' "$doc" | jq -e '.turns == 1' >/dev/null || fail ".turns was not 1"
printf '%s' "$doc" | jq -e 'has("tokens") and has("duration_ms") and has("session")' >/dev/null \
  || fail "json document is missing a required key"
                                                                                    
                                                                                      
printf '%s' "$doc" | jq -er '.session | startswith("inbound:cli:")' >/dev/null \
  || fail "session key is not an inbound:cli: key — the headless posture did not apply"

                                                                                  
echo "smoke: [3/4] --format streaming-json" >&2
nd="$(gideon run -p "$PROMPT" --format streaming-json)" || fail "streaming run exited nonzero"
[[ -n "$nd" ]] || fail "streaming-json produced no frames"
printf '%s\n' "$nd" | while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  printf '%s' "$line" | jq -e . >/dev/null || { echo "smoke: bad NDJSON line: $line" >&2; exit 1; }
done
printf '%s\n' "$nd" | jq -es 'map(.type) | index("chat_done") != null' >/dev/null \
  || fail "streaming-json never emitted chat_done"

                                                                                  
echo "smoke: [4/4] blank prompt is refused" >&2
if gideon run -p "" >/dev/null 2>&1; then
  fail "an empty prompt was accepted — the guard is inert"
fi

echo "smoke: PASS" >&2
