#!/bin/bash
# ════════════════════════════════════════════════════════════════════
# DataOne - Test Deployment Script
# ════════════════════════════════════════════════════════════════════
#
# This script validates a deployed DataOne Lakehouse App instance
# to ensure it's ready for marketplace submission.
#
# Usage:
#   ./test-deployment.sh [OPTIONS]
#
# Options:
#   -u, --app-url URL        App base URL (required)
#   -t, --token TOKEN        Test user access token (optional)
#   -v, --verbose            Verbose output
#   -h, --help               Show this help message
#
# Examples:
#   # Basic health check
#   ./test-deployment.sh -u https://workspace.cloud.databricks.com/apps/dataone-123
#
#   # Full test with authentication
#   ./test-deployment.sh -u https://workspace.cloud.databricks.com/apps/dataone-123 -t dapi123abc
#
# ════════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
APP_URL=""
TOKEN=""
VERBOSE=false

log() { echo -e "${BLUE}[TEST]${NC} $1"; }
error() { echo -e "${RED}[FAIL]${NC} $1" >&2; }
success() { echo -e "${GREEN}[PASS]${NC} $1"; }
warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }

show_help() {
    sed -n '2,/^# ═\+$/p' "$0" | sed 's/^# \?//'
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -u|--app-url) APP_URL="$2"; shift 2 ;;
        -t|--token) TOKEN="$2"; shift 2 ;;
        -v|--verbose) VERBOSE=true; shift ;;
        -h|--help) show_help; exit 0 ;;
        *) error "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

# Validate
if [[ -z "$APP_URL" ]]; then
    error "App URL is required. Use -u or --app-url option."
    show_help
    exit 1
fi

# Remove trailing slash
APP_URL="${APP_URL%/}"

log "Starting deployment validation..."
log "App URL: ${APP_URL}"
echo ""

PASS_COUNT=0
FAIL_COUNT=0

run_test() {
    local test_name="$1"
    local command="$2"
    
    log "Running: ${test_name}"
    
    if [[ "$VERBOSE" == true ]]; then
        echo "Command: ${command}"
    fi
    
    if eval "$command"; then
        success "${test_name}"
        ((PASS_COUNT++))
        return 0
    else
        error "${test_name}"
        ((FAIL_COUNT++))
        return 1
    fi
}

# ════════════════════════════════════════════════════════════════════
# Test 1: Frontend Accessibility
# ════════════════════════════════════════════════════════════════════

echo ""
log "═══ Frontend Tests ═══"
echo ""

run_test "Frontend homepage loads" \
    "curl -s -o /dev/null -w '%{http_code}' '${APP_URL}/' | grep -q '200'"

run_test "Frontend health check" \
    "curl -s '${APP_URL}/health' | grep -q 'healthy'"

# ════════════════════════════════════════════════════════════════════
# Test 2: Backend API Accessibility
# ════════════════════════════════════════════════════════════════════

echo ""
log "═══ Backend API Tests ═══"
echo ""

run_test "Backend health endpoint" \
    "curl -s '${APP_URL}/api/v1/health' | grep -q 'status'"

run_test "Backend version endpoint" \
    "curl -s '${APP_URL}/api/v1/version' | grep -q 'version'"

run_test "OAuth status endpoint" \
    "curl -s '${APP_URL}/api/v1/auth/databricks/status' | grep -q 'enabled'"

# ════════════════════════════════════════════════════════════════════
# Test 3: Authentication (if token provided)
# ════════════════════════════════════════════════════════════════════

if [[ -n "$TOKEN" ]]; then
    echo ""
    log "═══ Authentication Tests ═══"
    echo ""
    
    run_test "OAuth login redirect" \
        "curl -s -o /dev/null -w '%{http_code}' '${APP_URL}/api/v1/auth/databricks/login' | grep -q '302'"
    
    run_test "Protected endpoint (with auth)" \
        "curl -s -H 'Authorization: Bearer ${TOKEN}' '${APP_URL}/api/v1/connectors/' | grep -q '\['"
else
    warning "Skipping authentication tests (no token provided)"
fi

# ════════════════════════════════════════════════════════════════════
# Test 4: Security Headers
# ════════════════════════════════════════════════════════════════════

echo ""
log "═══ Security Tests ═══"
echo ""

run_test "HTTPS redirect enforced" \
    "curl -s -I '${APP_URL}/' | grep -qi 'strict-transport-security'"

run_test "X-Frame-Options header present" \
    "curl -s -I '${APP_URL}/' | grep -qi 'x-frame-options'"

run_test "X-Content-Type-Options header present" \
    "curl -s -I '${APP_URL}/' | grep -qi 'x-content-type-options'"

# ════════════════════════════════════════════════════════════════════
# Test 5: Performance
# ════════════════════════════════════════════════════════════════════

echo ""
log "═══ Performance Tests ═══"
echo ""

RESPONSE_TIME=$(curl -s -o /dev/null -w '%{time_total}' "${APP_URL}/")
RESPONSE_TIME_MS=$(echo "$RESPONSE_TIME * 1000" | bc)

if (( $(echo "$RESPONSE_TIME < 3" | bc -l) )); then
    success "Frontend response time: ${RESPONSE_TIME_MS}ms (< 3000ms)"
    ((PASS_COUNT++))
else
    warning "Frontend response time: ${RESPONSE_TIME_MS}ms (> 3000ms)"
fi

# ════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "                           TEST SUMMARY"
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo "  Total Tests: $((PASS_COUNT + FAIL_COUNT))"
echo "  Passed: ${PASS_COUNT}"
echo "  Failed: ${FAIL_COUNT}"
echo ""

if [[ $FAIL_COUNT -eq 0 ]]; then
    success "All tests passed! Deployment is ready for marketplace submission."
    exit 0
else
    error "Some tests failed. Review the output above and fix issues before submitting."
    exit 1
fi
