//! Validation rules used by the GUI settings dialog.

pub const TIMEOUT_ERROR: &str = "Timeouts must be whole numbers (page: 10–600, export: 30–3600).";

/// Validate user-entered timeout fields, returning page and export seconds.
pub fn validate_timeout_values(page: &str, export: &str) -> Result<(u32, u32), &'static str> {
    let page = page.trim().parse::<i64>().map_err(|_| TIMEOUT_ERROR)?;
    let export = export.trim().parse::<i64>().map_err(|_| TIMEOUT_ERROR)?;
    if !(10..=600).contains(&page) || !(30..=3600).contains(&export) {
        return Err(TIMEOUT_ERROR);
    }
    Ok((page as u32, export as u32))
}

/// Normalize stored integer settings; invalid inputs use the given default.
pub fn normalized_int(value: &str, default: i64, minimum: i64, maximum: i64) -> i64 {
    value.trim().parse::<i64>().map_or(default, |number| number.clamp(minimum, maximum))
}

/// Select the closest supported batch size, preferring the smaller on ties.
pub fn normalize_batch_size(value: &str) -> u32 {
    let number = normalized_int(value, 8, 1, 64);
    [4, 8, 16].into_iter().min_by_key(|option| (i64::from(*option) - number).abs()).unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timeout_bounds_and_invalid_input() {
        assert_eq!(validate_timeout_values(" 10 ", "3600"), Ok((10, 3600)));
        assert_eq!(validate_timeout_values("600", "30"), Ok((600, 30)));
        for (page, export) in [("9", "30"), ("601", "30"), ("10", "29"), ("10", "3601"), ("1.5", "60"), ("", "60")] {
            assert_eq!(validate_timeout_values(page, export), Err(TIMEOUT_ERROR));
        }
    }

    #[test]
    fn batch_normalization() {
        for (input, expected) in [("invalid", 8), ("0", 4), ("6", 4), ("12", 8), ("99", 16)] {
            assert_eq!(normalize_batch_size(input), expected);
        }
        assert_eq!(normalized_int("1000", 120, 10, 600), 600);
        assert_eq!(normalized_int("invalid", 120, 10, 600), 120);
    }
}
