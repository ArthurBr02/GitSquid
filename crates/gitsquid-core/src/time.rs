//! Dates in the shape `git log --pretty=%aI` produces: strict ISO 8601, with the offset the
//! author actually committed under. Written out rather than pulled from a crate — it is one
//! well-known algorithm, and the interface parses the result as text either way.

/// Days from 1970-01-01 to a civil year/month/day, after Howard Hinnant's `civil_from_days`.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_position = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * month_position + 2) / 5 + 1) as u32;
    let month = (month_position + if month_position < 10 { 3 } else { -9 }) as u32;
    (year + i64::from(month <= 2), month, day)
}

/// `seconds` is a Unix timestamp; `offset_minutes` is the author's own offset from UTC.
pub fn iso8601(seconds: i64, offset_minutes: i32) -> String {
    let local = seconds + i64::from(offset_minutes) * 60;
    let days = local.div_euclid(86_400);
    let rest = local.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let (hour, minute, second) = (rest / 3600, (rest % 3600) / 60, rest % 60);

    let sign = if offset_minutes < 0 { '-' } else { '+' };
    let offset = offset_minutes.abs();
    format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{sign}{:02}:{:02}",
        offset / 60,
        offset % 60
    )
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_epoch_itself() {
        assert_eq!(iso8601(0, 0), "1970-01-01T00:00:00+00:00");
    }

    #[test]
    fn a_known_instant_in_utc() {
        // 2026-08-26T09:30:15Z
        assert_eq!(iso8601(1_787_736_615, 0), "2026-08-26T09:30:15+00:00");
    }

    #[test]
    fn the_offset_moves_the_clock_not_the_instant() {
        assert_eq!(iso8601(1_787_736_615, 120), "2026-08-26T11:30:15+02:00");
        assert_eq!(iso8601(1_787_736_615, -300), "2026-08-26T04:30:15-05:00");
    }

    #[test]
    fn an_offset_that_is_not_a_whole_hour() {
        assert_eq!(iso8601(0, 330), "1970-01-01T05:30:00+05:30");
        assert_eq!(iso8601(0, -570), "1969-12-31T14:30:00-09:30");
    }

    #[test]
    fn a_leap_day_lands_where_it_should() {
        // 2024-02-29T12:00:00Z
        assert_eq!(iso8601(1_709_208_000, 0), "2024-02-29T12:00:00+00:00");
    }

    #[test]
    fn a_date_before_the_epoch_still_reads() {
        assert_eq!(iso8601(-1, 0), "1969-12-31T23:59:59+00:00");
    }

    #[test]
    fn a_century_boundary_is_not_a_leap_year() {
        // 1900 was not a leap year, 2000 was.
        assert_eq!(iso8601(951_782_400, 0), "2000-02-29T00:00:00+00:00");
    }

    #[test]
    fn dates_sort_correctly_as_plain_text() {
        // The search results are ordered on this string, so lexical order must be time order.
        let mut stamps = [iso8601(2_000_000_000, 0), iso8601(0, 0), iso8601(1_000_000_000, 0)];
        stamps.sort();
        assert_eq!(stamps[0], iso8601(0, 0));
        assert_eq!(stamps[2], iso8601(2_000_000_000, 0));
    }
}

/// `git log --format=%cr`: how long ago, in the words git uses. Thresholds follow git's own
/// `show_date_relative`, so a stash reads the same here as in the terminal.
pub fn relative(then: i64, now: i64) -> String {
    use crate::phrasing::plural;

    let elapsed = now.saturating_sub(then);
    if elapsed < 0 {
        return "in the future".to_string();
    }
    let seconds = elapsed;
    if seconds < 90 {
        return format!("{} ago", plural(seconds as usize, "second"));
    }
    let minutes = (seconds + 30) / 60;
    if minutes < 90 {
        return format!("{} ago", plural(minutes as usize, "minute"));
    }
    let hours = (minutes + 30) / 60;
    if hours < 36 {
        return format!("{} ago", plural(hours as usize, "hour"));
    }
    let days = (hours + 12) / 24;
    if days < 14 {
        return format!("{} ago", plural(days as usize, "day"));
    }
    if days < 70 {
        return format!("{} ago", plural(((days + 3) / 7) as usize, "week"));
    }
    if days < 365 {
        return format!("{} ago", plural(((days + 15) / 30) as usize, "month"));
    }
    format!("{} ago", plural(((days + 183) / 365) as usize, "year"))
}

pub fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs() as i64)
        .unwrap_or_default()
}

#[cfg(test)]
mod relative_tests {
    use super::relative;

    const MINUTE: i64 = 60;
    const HOUR: i64 = 60 * MINUTE;
    const DAY: i64 = 24 * HOUR;

    #[test]
    fn seconds_then_minutes_then_hours() {
        assert_eq!(relative(0, 5), "5 seconds ago");
        assert_eq!(relative(0, 5 * MINUTE), "5 minutes ago");
        assert_eq!(relative(0, 5 * HOUR), "5 hours ago");
    }

    #[test]
    fn days_weeks_months_and_years() {
        assert_eq!(relative(0, 3 * DAY), "3 days ago");
        assert_eq!(relative(0, 21 * DAY), "3 weeks ago");
        assert_eq!(relative(0, 120 * DAY), "4 months ago");
        assert_eq!(relative(0, 800 * DAY), "2 years ago");
    }

    #[test]
    fn a_single_second_is_singular() {
        assert_eq!(relative(0, 1), "1 second ago");
    }

    #[test]
    fn the_thresholds_step_straight_over_the_singular() {
        // git's own arithmetic: each unit only takes over once it is worth at least two of
        // itself, so `git log --format=%cr` never prints "1 hour ago" or "1 day ago" either.
        assert_eq!(relative(0, 89 * MINUTE), "89 minutes ago");
        assert_eq!(relative(0, 90 * MINUTE), "2 hours ago");
        assert_eq!(relative(0, 35 * HOUR), "35 hours ago");
        assert_eq!(relative(0, 36 * HOUR), "2 days ago");
    }

    #[test]
    fn a_clock_that_ran_backwards_does_not_panic() {
        assert_eq!(relative(100, 0), "in the future");
    }
}
