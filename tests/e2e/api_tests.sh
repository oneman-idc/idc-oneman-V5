#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-http://localhost:3000}
HASHPAY_MOCK=${HASHPAY_MOCK:-http://localhost:8081}

echo "Starting E2E API test against ${BASE_URL} and HashPay mock ${HASHPAY_MOCK}"

ORDER_ID="e2e-$(date +%s)"
USER_EMAIL="e2e-test+${ORDER_ID}@example.com"
AMOUNT=9.99

# simulate payment: post to hashpay-mock simulate endpoint
echo "Simulating payment via HashPay-mock..."
curl -s -X POST -H "Content-Type: application/json" -d "{\"callback_url\": \"${BASE_URL}/api/payments/hashpay\", \"order_id\": \"${ORDER_ID}\", \"amount\": ${AMOUNT}, \"user_email\": \"${USER_EMAIL}\" }" ${HASHPAY_MOCK}/simulate | jq .

echo "Waiting 2s for webhook processing..."
sleep 2

# Check smtp4dev web UI to see email was received (smtp4dev provides API at /api/Messages)
SMTP_UI=${SMTP_UI:-http://localhost:5000}

echo "Fetching messages from smtp4dev web UI..."
curl -s ${SMTP_UI}/api/Messages | jq '.[0]'

echo "E2E API test finished. Check above outputs for created container and email." 
