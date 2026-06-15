# Shared timezone helpers for the launchd scheduler.
# This file is sourced by bash and zsh scripts; keep syntax POSIX-friendly.

AGENTIC_TRADER_REQUIRED_TZ="America/New_York"

agentic_trader_detect_host_timezone() {
  # Test seam: lets tests exercise mismatch handling without depending on the
  # timezone configured on the machine running the test suite.
  if [ -n "${AGENTIC_TRADER_HOST_TZ_OVERRIDE:-}" ]; then
    printf '%s\n' "$AGENTIC_TRADER_HOST_TZ_OVERRIDE"
    return 0
  fi
  if [ -n "${AGENTIC_TRADER_HOST_TZ:-}" ]; then
    printf '%s\n' "$AGENTIC_TRADER_HOST_TZ"
    return 0
  fi

  _agentic_trader_systemsetup_bin=""
  if [ -x /usr/sbin/systemsetup ]; then
    _agentic_trader_systemsetup_bin="/usr/sbin/systemsetup"
  else
    _agentic_trader_systemsetup_bin="$(command -v systemsetup 2>/dev/null || true)"
  fi

  if [ -n "$_agentic_trader_systemsetup_bin" ]; then
    _agentic_trader_tz_output="$("$_agentic_trader_systemsetup_bin" -gettimezone 2>/dev/null || true)"
    case "$_agentic_trader_tz_output" in
      "Time Zone: "?*)
        printf '%s\n' "${_agentic_trader_tz_output#Time Zone: }"
        return 0
        ;;
    esac
  fi

  if command -v timedatectl >/dev/null 2>&1; then
    _agentic_trader_timedatectl_tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    if [ -n "$_agentic_trader_timedatectl_tz" ]; then
      printf '%s\n' "$_agentic_trader_timedatectl_tz"
      return 0
    fi
  fi

  if [ -L /etc/localtime ]; then
    _agentic_trader_localtime_target="$(readlink /etc/localtime 2>/dev/null || true)"
    case "$_agentic_trader_localtime_target" in
      *zoneinfo/posix/?*)
        printf '%s\n' "${_agentic_trader_localtime_target##*zoneinfo/posix/}"
        return 0
        ;;
      *zoneinfo/right/?*)
        printf '%s\n' "${_agentic_trader_localtime_target##*zoneinfo/right/}"
        return 0
        ;;
      *zoneinfo/?*)
        printf '%s\n' "${_agentic_trader_localtime_target##*zoneinfo/}"
        return 0
        ;;
    esac
  fi

  if [ -r /etc/timezone ]; then
    IFS= read -r _agentic_trader_timezone_file < /etc/timezone || _agentic_trader_timezone_file=""
    if [ -n "$_agentic_trader_timezone_file" ]; then
      printf '%s\n' "$_agentic_trader_timezone_file"
      return 0
    fi
  fi

  _agentic_trader_tz_abbrev="$(date +%Z 2>/dev/null || true)"
  if [ -n "$_agentic_trader_tz_abbrev" ]; then
    printf '%s\n' "$_agentic_trader_tz_abbrev"
    return 0
  fi

  printf '%s\n' "unknown"
}

agentic_trader_is_eastern_timezone() {
  # Accept zone names that keep the 15:45 local launch aligned with the Eastern
  # trading window. Bare abbreviations (EST/EDT) are ambiguous and not DST-safe.
  case "$1" in
    America/New_York|US/Eastern|EST5EDT)
      return 0
      ;;
    America/Detroit|America/Toronto|America/Montreal|Canada/Eastern|America/Nassau)
      return 0
      ;;
    America/Indiana/Indianapolis|America/Indianapolis|America/Indiana/Marengo)
      return 0
      ;;
    America/Indiana/Petersburg|America/Indiana/Vevay|America/Indiana/Vincennes)
      return 0
      ;;
    America/Indiana/Winamac|America/Kentucky/Louisville|America/Kentucky/Monticello)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

agentic_trader_timezone_requirement_reason() {
  printf '%s\n' "launchd StartCalendarInterval uses the Mac's machine-local timezone, but agentic-trader's trading window is fixed to Eastern Time (${AGENTIC_TRADER_REQUIRED_TZ}). Install and run this scheduler only on an Eastern Time host so the 15:45 local launch aligns with the 15:30-15:58 ET trading window."
}
