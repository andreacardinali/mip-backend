export BASE_URL='http://127.0.0.1:8000'

read -rp 'Username MIP: ' MIP_USERNAME
read -srp 'Password MIP: ' MIP_PASSWORD
echo

export MIP_TOKEN="$(
  curl --fail-with-body \
    --silent \
    --show-error \
    -X POST \
    "${BASE_URL}/api/auth/login" \
    -H 'Content-Type: application/json' \
    --data "$(
      jq -n \
        --arg username "${MIP_USERNAME}" \
        --arg password "${MIP_PASSWORD}" \
        '{
          username: $username,
          password: $password
        }'
    )" |
  jq -er '.access_token'
)"

unset MIP_PASSWORD

echo "Token acquisito su \$MIP_TOKEN: ${#MIP_TOKEN} caratteri"
