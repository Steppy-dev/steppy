"""Test the new guide-text format with title, subtitle, and text."""

from steppy.parser import parse_scenario_file


def test_guide_text_structured_format(tmp_path):
    """Test parsing guide-text with new structured format (title, subtitle, text)."""
    content = """---
title: New Guide Format Test
description: Test the new structured guide-text format
tags:
  - test
  - user-guide-editor
guide:
  enabled: true
---

### 1. Navigate to dashboard
- role: editor
- action: navigate
- target: /dashboard
- screenshot: editor
- guide-text:
    fr:
      title: "Accéder au tableau de bord"
      subtitle: "Navigation principale"
      text: "Depuis le menu principal, cliquez sur 'Tableau de bord' pour voir vos statistiques et notifications."
    en:
      title: "Access dashboard"
      subtitle: "Main navigation"
      text: "From the main menu, click 'Dashboard' to see your statistics and notifications."

### 2. View notifications
- role: editor
- action: click
- selector: [data-testid="notifications-btn"]
- screenshot: editor
- guide-text:
    fr:
      title: "Consulter les notifications"
      text: "Cliquez sur l'icône de notification en haut à droite."
    en:
      title: "View notifications"
      text: "Click the notification icon in the top right corner."

### 3. Test minimal format (title only)
- role: editor
- action: click
- selector: [data-testid="settings-btn"]
- screenshot: editor
- guide-text:
    fr:
      title: "Accéder aux paramètres"
    en:
      title: "Access settings"
"""

    scenario_file = tmp_path / "test_new_format.md"
    scenario_file.write_text(content, encoding="utf-8")

    scenario = parse_scenario_file(scenario_file)

    # Verify scenario parsed correctly
    assert scenario.title == "New Guide Format Test"
    assert len(scenario.steps) == 3
    assert scenario.guide.enabled is True

    # Test step 1 - full format (title + subtitle + text)
    step1 = scenario.steps[0]
    assert step1.guide_text is not None
    assert step1.guide_text.fr is not None
    assert step1.guide_text.fr.title == "Accéder au tableau de bord"
    assert step1.guide_text.fr.subtitle == "Navigation principale"
    assert (
        step1.guide_text.fr.text
        == "Depuis le menu principal, cliquez sur 'Tableau de bord' pour voir vos statistiques et notifications."
    )

    assert step1.guide_text.en is not None
    assert step1.guide_text.en.title == "Access dashboard"
    assert step1.guide_text.en.subtitle == "Main navigation"
    assert step1.guide_text.en.text == "From the main menu, click 'Dashboard' to see your statistics and notifications."

    # Test step 2 - title + text (no subtitle)
    step2 = scenario.steps[1]
    assert step2.guide_text is not None
    assert step2.guide_text.fr is not None
    assert step2.guide_text.fr.title == "Consulter les notifications"
    assert step2.guide_text.fr.subtitle is None
    assert step2.guide_text.fr.text == "Cliquez sur l'icône de notification en haut à droite."

    # Test step 3 - title only (no subtitle or text)
    step3 = scenario.steps[2]
    assert step3.guide_text is not None
    assert step3.guide_text.fr is not None
    assert step3.guide_text.fr.title == "Accéder aux paramètres"
    assert step3.guide_text.fr.subtitle is None
    assert step3.guide_text.fr.text is None


def test_guide_text_backward_compatibility(tmp_path):
    """Test that old string format still works (backward compatibility)."""
    content = """---
title: Backward Compatibility Test
guide:
  enabled: true
---

### 1. Old format step
- role: editor
- action: click
- selector: button
- screenshot: editor
- guide-text:
    fr: "Cliquez sur le bouton. C'est un test de compatibilité."
    en: "Click the button. This is a compatibility test."
"""

    scenario_file = tmp_path / "test_old_format.md"
    scenario_file.write_text(content, encoding="utf-8")

    scenario = parse_scenario_file(scenario_file)

    assert len(scenario.steps) == 1
    step = scenario.steps[0]
    assert step.guide_text is not None

    # Old string format should be normalized to structured format
    # Title is extracted from first sentence
    assert step.guide_text.fr is not None
    assert step.guide_text.fr.title == "Cliquez sur le bouton"
    assert step.guide_text.fr.subtitle is None
    assert step.guide_text.fr.text == "Cliquez sur le bouton. C'est un test de compatibilité."

    assert step.guide_text.en is not None
    assert step.guide_text.en.title == "Click the button"
    assert step.guide_text.en.subtitle is None
    assert step.guide_text.en.text == "Click the button. This is a compatibility test."


def test_guide_text_mixed_formats(tmp_path):
    """Test mixing old and new formats in the same scenario."""
    content = """---
title: Mixed Format Test
guide:
  enabled: true
---

### 1. New format
- role: editor
- action: click
- selector: button1
- screenshot: editor
- guide-text:
    fr:
      title: "Nouveau format"
      subtitle: "Avec sous-titre"
      text: "Description complète."

### 2. Old format
- role: editor
- action: click
- selector: button2
- screenshot: editor
- guide-text:
    fr: "Ancien format. Description simple."
"""

    scenario_file = tmp_path / "test_mixed_format.md"
    scenario_file.write_text(content, encoding="utf-8")

    scenario = parse_scenario_file(scenario_file)

    assert len(scenario.steps) == 2

    # New format step
    step1 = scenario.steps[0]
    assert step1.guide_text.fr.title == "Nouveau format"
    assert step1.guide_text.fr.subtitle == "Avec sous-titre"
    assert step1.guide_text.fr.text == "Description complète."

    # Old format step
    step2 = scenario.steps[1]
    assert step2.guide_text.fr.title == "Ancien format"
    assert step2.guide_text.fr.subtitle is None
    assert step2.guide_text.fr.text == "Ancien format. Description simple."


def test_guide_text_no_period_in_string(tmp_path):
    """Test old string format without a period."""
    content = """---
title: No Period Test
---

### 1. Test step
- role: editor
- action: click
- selector: button
- guide-text:
    fr: "Cliquez ici"
"""

    scenario_file = tmp_path / "test_no_period.md"
    scenario_file.write_text(content, encoding="utf-8")

    scenario = parse_scenario_file(scenario_file)

    step = scenario.steps[0]
    # When there's no period, the whole string becomes the title
    assert step.guide_text.fr.title == "Cliquez ici"
    assert step.guide_text.fr.text == "Cliquez ici"
