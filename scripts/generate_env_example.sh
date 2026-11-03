#!/usr/bin/env bash

# Generate a shareable .env.example from a dotenv file.
#
# Only values classified as sensitive are replaced.
# Non-sensitive configuration is preserved, including:
#   - ports, timeouts, retries, limits and TTLs
#   - booleans and numeric values
#   - provider and model names
#   - public or local URLs
#   - timezones, algorithms, intervals and periods
#   - provider orders and other lists
#   - JSON configuration without recognizable secrets
#
# Preserved formatting:
#   - comments
#   - blank lines
#   - ordering
#   - optional "export" prefix
#   - spaces around "="
#   - outer quotes
#   - inline comments
#
# The source file is never modified.
#
# Usage:
#   ./generate_env_example.sh
#   ./generate_env_example.sh .env
#   ./generate_env_example.sh .env .env.example
#   ./generate_env_example.sh --force .env .env.example

set -o errexit
set -o nounset
set -o pipefail

readonly DEFAULT_INPUT=".env"
readonly DEFAULT_OUTPUT=".env.example"
readonly SECRET_PLACEHOLDER="<REQUIRED_SECRET>"
readonly PRIVATE_VALUE_PLACEHOLDER="<REDACTED>"

FORCE=0
INPUT_PATH=""
OUTPUT_PATH=""

usage() {
    cat <<'EOF'
Usage:
  generate_env_example.sh [OPTIONS] [INPUT [OUTPUT]]

Arguments:
  INPUT                 Source dotenv file. Default: .env
  OUTPUT                Destination file. Default: .env.example

Options:
  -f, --force           Overwrite OUTPUT if it already exists
  -h, --help            Show this help

Examples:
  ./generate_env_example.sh
  ./generate_env_example.sh .env
  ./generate_env_example.sh .env .env.example
  ./generate_env_example.sh --force .env .env.example
EOF
}

error() {
    printf 'Error: %s\n' "$*" >&2
}

while (($# > 0)); do
    case "$1" in
        -f|--force)
            FORCE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            error "unknown option: $1"
            usage >&2
            exit 2
            ;;
        *)
            if [[ -z "$INPUT_PATH" ]]; then
                INPUT_PATH="$1"
            elif [[ -z "$OUTPUT_PATH" ]]; then
                OUTPUT_PATH="$1"
            else
                error "too many positional arguments"
                usage >&2
                exit 2
            fi
            shift
            ;;
    esac
done

if (($# > 0)); then
    if [[ -z "$INPUT_PATH" ]]; then
        INPUT_PATH="$1"
        shift
    fi
fi

if (($# > 0)); then
    if [[ -z "$OUTPUT_PATH" ]]; then
        OUTPUT_PATH="$1"
        shift
    fi
fi

if (($# > 0)); then
    error "too many positional arguments"
    usage >&2
    exit 2
fi

INPUT_PATH="${INPUT_PATH:-$DEFAULT_INPUT}"
OUTPUT_PATH="${OUTPUT_PATH:-$DEFAULT_OUTPUT}"

if [[ ! -f "$INPUT_PATH" ]]; then
    error "input file not found: $INPUT_PATH"
    exit 2
fi

input_real_path="$(
    cd -- "$(dirname -- "$INPUT_PATH")"
    printf '%s/%s\n' "$PWD" "$(basename -- "$INPUT_PATH")"
)"

output_directory="$(dirname -- "$OUTPUT_PATH")"
output_basename="$(basename -- "$OUTPUT_PATH")"

if [[ ! -d "$output_directory" ]]; then
    error "output directory does not exist: $output_directory"
    exit 2
fi

output_real_path="$(
    cd -- "$output_directory"
    printf '%s/%s\n' "$PWD" "$output_basename"
)"

if [[ "$input_real_path" == "$output_real_path" ]]; then
    error "output must differ from input"
    exit 2
fi

if [[ -e "$OUTPUT_PATH" && "$FORCE" -ne 1 ]]; then
    error "output already exists: $OUTPUT_PATH"
    printf 'Use --force to overwrite it.\n' >&2
    exit 2
fi

is_sensitive_key() {
    local key="${1^^}"

    # Exact keys whose value must never be published.
    case "$key" in
        DATABASE_URL)
            return 0
            ;;
        JWT_SECRET)
            return 0
            ;;
        GHOSTFOLIO_SECURITY_TOKEN)
            return 0
            ;;
        NOTIFICATIONS_CREDENTIAL_ENCRYPTION_KEY)
            return 0
            ;;
        TELEGRAM_BOT_TOKEN)
            return 0
            ;;
        TELEGRAM_CHAT_ID)
            return 0
            ;;
    esac

    # Sensitive key components.
    #
    # Delimiters prevent false positives such as:
    #   JWT_ALGORITHM
    #   TOKEN_LIMIT
    #   SESSION_TIMEOUT_SECONDS
    #   AUTH_METHOD
    if [[ "$key" =~ (^|_)(API_KEY|SECRET|CLIENT_SECRET|PRIVATE_KEY|PASSWORD|PASSWD|ACCESS_TOKEN|REFRESH_TOKEN|AUTH_TOKEN|SECURITY_TOKEN|BOT_TOKEN|CREDENTIAL_ENCRYPTION_KEY|CONNECTION_STRING|DATABASE_URL|DSN|WEBHOOK_SECRET|COOKIE_SECRET|SESSION_SECRET|CHAT_ID)($|_) ]]; then
        return 0
    fi

    return 1
}

is_sensitive_value() {
    local value="$1"
    local lower_value="${value,,}"

    # URI containing embedded credentials:
    # protocol://username:password@host
    if [[ "$value" =~ ^[A-Za-z][A-Za-z0-9+.-]*://[^/@:]+:[^/@]+@ ]]; then
        return 0
    fi

    # Common private-key material.
    if [[ "$value" == *"-----BEGIN "*"PRIVATE KEY-----"* ]]; then
        return 0
    fi

    # JSON configuration containing recognizable secret property names.
    # Ordinary provider-native JSON configuration remains visible.
    if [[ "$value" == \{* || "$value" == \[* ]]; then
        if [[ "$lower_value" =~ \"(api_key|apikey|secret|client_secret|password|access_token|refresh_token|authorization|bearer_token|private_key)\"[[:space:]]*: ]]; then
            return 0
        fi
    fi

    return 1
}

replacement_for_key() {
    local key="${1^^}"

    case "$key" in
        DATABASE_URL|*CONNECTION_STRING*|*DSN*)
            printf '%s' "$PRIVATE_VALUE_PLACEHOLDER"
            ;;
        TELEGRAM_CHAT_ID)
            printf '%s' "$PRIVATE_VALUE_PLACEHOLDER"
            ;;
        *)
            printf '%s' "$SECRET_PLACEHOLDER"
            ;;
    esac
}

split_inline_comment() {
    local raw="$1"
    local length="${#raw}"
    local index
    local character
    local previous=""
    local quote=""
    local escaped=0

    SPLIT_VALUE="$raw"
    SPLIT_COMMENT=""

    for ((index = 0; index < length; index++)); do
        character="${raw:index:1}"

        if ((escaped)); then
            escaped=0
            previous="$character"
            continue
        fi

        if [[ "$character" == '\' ]]; then
            escaped=1
            previous="$character"
            continue
        fi

        if [[ -n "$quote" ]]; then
            if [[ "$character" == "$quote" ]]; then
                quote=""
            fi
            previous="$character"
            continue
        fi

        if [[ "$character" == "'" || "$character" == '"' ]]; then
            quote="$character"
            previous="$character"
            continue
        fi

        if [[ "$character" == "#" ]]; then
            if ((index == 0)) || [[ "$previous" =~ [[:space:]] ]]; then
                local before_comment="${raw:0:index}"
                local trimmed_value="$before_comment"

                while [[ "$trimmed_value" =~ [[:space:]]$ ]]; do
                    trimmed_value="${trimmed_value%?}"
                done

                local spacing="${before_comment:${#trimmed_value}}"

                SPLIT_VALUE="$trimmed_value"
                SPLIT_COMMENT="${spacing}${raw:index}"
                return
            fi
        fi

        previous="$character"
    done
}

extract_outer_quote() {
    local value="$1"
    local trimmed="$value"

    while [[ "$trimmed" =~ ^[[:space:]] ]]; do
        trimmed="${trimmed:1}"
    done

    while [[ "$trimmed" =~ [[:space:]]$ ]]; do
        trimmed="${trimmed%?}"
    done

    OUTER_QUOTE=""
    INNER_VALUE="$trimmed"

    if ((${#trimmed} >= 2)); then
        local first="${trimmed:0:1}"
        local last="${trimmed: -1}"

        if [[ "$first" == "$last" && ( "$first" == "'" || "$first" == '"' ) ]]; then
            OUTER_QUOTE="$first"
            INNER_VALUE="${trimmed:1:${#trimmed}-2}"
        fi
    fi
}

anonymize_assignment() {
    local line="$1"

    # Capture:
    #   1. indentation
    #   2. optional export prefix
    #   3. variable name
    #   4. separator including spaces around "="
    #   5. raw value
    if [[ "$line" =~ ^([[:space:]]*)(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)([[:space:]]*=[[:space:]]*)(.*)$ ]]; then
        local indent="${BASH_REMATCH[1]}"
        local export_prefix="${BASH_REMATCH[2]}"
        local key="${BASH_REMATCH[3]}"
        local separator="${BASH_REMATCH[4]}"
        local raw_value="${BASH_REMATCH[5]}"

        split_inline_comment "$raw_value"
        extract_outer_quote "$SPLIT_VALUE"

        local final_value="$SPLIT_VALUE"

        if [[ -n "$INNER_VALUE" ]]; then
            if is_sensitive_key "$key" || is_sensitive_value "$INNER_VALUE"; then
                local replacement
                replacement="$(replacement_for_key "$key")"

                if [[ -n "$OUTER_QUOTE" ]]; then
                    final_value="${OUTER_QUOTE}${replacement}${OUTER_QUOTE}"
                else
                    final_value="$replacement"
                fi
            fi
        fi

        printf '%s%s%s%s%s%s\n' \
            "$indent" \
            "$export_prefix" \
            "$key" \
            "$separator" \
            "$final_value" \
            "$SPLIT_COMMENT"

        return 0
    fi

    return 1
}

temporary_output="$(mktemp "${output_directory}/.${output_basename}.tmp.XXXXXX")"

cleanup() {
    rm -f -- "$temporary_output"
}

trap cleanup EXIT INT TERM

variable_count=0
redacted_count=0
line_number=0

while IFS= read -r line || [[ -n "$line" ]]; do
    ((line_number += 1))

    # Remove CR from CRLF input. Output is normalized to LF.
    line="${line%$'\r'}"

    stripped="${line#"${line%%[![:space:]]*}"}"

    if [[ -z "$stripped" || "$stripped" == \#* ]]; then
        printf '%s\n' "$line" >> "$temporary_output"
        continue
    fi

    if [[ "$line" =~ ^([[:space:]]*)(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)([[:space:]]*=[[:space:]]*)(.*)$ ]]; then
        key="${BASH_REMATCH[3]}"
        raw_value="${BASH_REMATCH[5]}"

        split_inline_comment "$raw_value"
        extract_outer_quote "$SPLIT_VALUE"

        ((variable_count += 1))

        if [[ -n "$INNER_VALUE" ]] &&
           { is_sensitive_key "$key" || is_sensitive_value "$INNER_VALUE"; }; then
            ((redacted_count += 1))
        fi

        anonymize_assignment "$line" >> "$temporary_output"
    else
        printf '# [ENV-EXAMPLE: unrecognized line %d removed]\n' \
            "$line_number" >> "$temporary_output"
    fi
done < "$INPUT_PATH"

chmod 600 "$temporary_output"
mv -- "$temporary_output" "$OUTPUT_PATH"
trap - EXIT INT TERM

printf 'Created: %s\n' "$OUTPUT_PATH"
printf 'Variables processed: %d\n' "$variable_count"
printf 'Sensitive values replaced: %d\n' "$redacted_count"
printf 'Original file was not modified.\n'