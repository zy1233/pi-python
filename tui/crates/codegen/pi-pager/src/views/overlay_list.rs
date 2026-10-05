//! Shared prompt-area list overlay: accent bar, bold title, and a
//! scrollable single-line row list with a cursor.
//!
//! One source of truth for the row geometry that `/rewind`'s picker phase
//! and `/jump` previously each kept in sync by hand across their render,
//! hit-test, and height functions. Row *content* stays with the caller
//! (a closure); this owns chrome, cursor styling, and the scroll window.


#[cfg(test)]
mod tests {

}
