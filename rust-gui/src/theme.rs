//! Shared visual language for the desktop interface.
use eframe::egui::{self, Color32, Frame, Margin, RichText, Rounding, Stroke, Ui, Vec2};

pub const ACCENT: Color32 = Color32::from_rgb(12, 122, 120);
pub const ACCENT_LIGHT: Color32 = Color32::from_rgb(20, 144, 140);

/// Prefer the native Windows UI font when available; retain egui's built-in
/// fonts as fallbacks for other systems and missing glyphs.
pub fn install_fonts(ctx: &egui::Context) {
    if let Some(root) = std::env::var_os("WINDIR") {
        let path = std::path::PathBuf::from(root).join("Fonts").join("segoeui.ttf");
        if let Ok(bytes) = std::fs::read(path) {
            let mut fonts = egui::FontDefinitions::default();
            fonts.font_data.insert("segoe-ui".into(), egui::FontData::from_owned(bytes));
            fonts.families.entry(egui::FontFamily::Proportional).or_default().insert(0, "segoe-ui".into());
            ctx.set_fonts(fonts);
        }
    }
}

pub fn apply(ctx: &egui::Context, dark: bool) {
    let mut visuals = if dark { egui::Visuals::dark() } else { egui::Visuals::light() };
    let (background, card, field, border, text) = if dark {
        (Color32::from_rgb(19, 21, 24), Color32::from_rgb(28, 31, 36), Color32::from_rgb(20, 23, 27), Color32::from_rgb(49, 55, 62), Color32::from_rgb(232, 234, 237))
    } else {
        (Color32::from_rgb(242, 243, 245), Color32::WHITE, Color32::from_rgb(247, 248, 250), Color32::from_rgb(218, 222, 226), Color32::from_rgb(26, 29, 33))
    };
    visuals.panel_fill = background;
    visuals.window_fill = card;
    visuals.extreme_bg_color = field;
    visuals.faint_bg_color = field;
    visuals.override_text_color = Some(text);
    visuals.window_rounding = Rounding::same(14.0);
    visuals.window_stroke = Stroke::new(1.0_f32, border);
    visuals.hyperlink_color = if dark { ACCENT_LIGHT } else { ACCENT };
    visuals.selection.bg_fill = if dark { ACCENT_LIGHT } else { ACCENT };
    visuals.selection.stroke = Stroke::new(1.0_f32, Color32::WHITE);
    for widget in [&mut visuals.widgets.inactive, &mut visuals.widgets.hovered, &mut visuals.widgets.active, &mut visuals.widgets.open] {
        widget.rounding = Rounding::same(8.0);
        widget.bg_stroke = Stroke::new(1.0_f32, border);
    }
    visuals.widgets.inactive.bg_fill = field;
    visuals.widgets.hovered.bg_fill = if dark { Color32::from_rgb(42, 48, 52) } else { Color32::from_rgb(234, 241, 240) };
    visuals.widgets.active.bg_fill = if dark { Color32::from_rgb(34, 80, 78) } else { Color32::from_rgb(216, 237, 235) };
    ctx.set_visuals(visuals);
    let mut style = (*ctx.style()).clone();
    style.spacing.item_spacing = Vec2::new(8.0, 8.0);
    style.spacing.button_padding = Vec2::new(14.0, 9.0);
    style.spacing.interact_size.y = 36.0;
    ctx.set_style(style);
}

pub fn muted(ui: &Ui) -> Color32 {
    if ui.visuals().dark_mode { Color32::from_rgb(154, 163, 174) } else { Color32::from_rgb(95, 104, 115) }
}

pub fn card(ui: &mut Ui, add: impl FnOnce(&mut Ui)) {
    let dark = ui.visuals().dark_mode;
    Frame::none()
        .fill(if dark { Color32::from_rgb(28, 31, 36) } else { Color32::WHITE })
        .stroke(Stroke::new(1.0_f32, if dark { Color32::from_rgb(39, 44, 51) } else { Color32::from_rgb(227, 230, 234) }))
        .rounding(Rounding::same(14.0))
        .inner_margin(Margin::same(18.0))
        .show(ui, |ui| { ui.set_min_width(ui.available_width()); add(ui); });
}

pub fn primary(label: &str) -> egui::Button<'_> {
    egui::Button::new(RichText::new(label).strong().color(Color32::WHITE))
        .fill(ACCENT).stroke(Stroke::NONE).rounding(8.0).min_size(Vec2::new(135.0, 40.0))
}

pub fn section(ui: &mut Ui, title: &str, caption: &str) {
    ui.label(RichText::new(title).size(16.0).strong());
    if !caption.is_empty() { ui.label(RichText::new(caption).size(12.0).color(muted(ui))); }
    ui.add_space(5.0);
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn both_appearances_apply_consistent_accent_and_rounding() {
        let ctx = egui::Context::default();
        apply(&ctx, false);
        assert!(!ctx.style().visuals.dark_mode);
        assert_eq!(ctx.style().visuals.hyperlink_color, ACCENT);
        assert_eq!(ctx.style().visuals.window_rounding, Rounding::same(14.0));
        apply(&ctx, true);
        assert!(ctx.style().visuals.dark_mode);
        assert_eq!(ctx.style().visuals.hyperlink_color, ACCENT_LIGHT);
    }
}
