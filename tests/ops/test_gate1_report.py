"""Tests for Automated Gate 1 Report Generator CLI Script."""

import pytest

from scripts.generate_gate1_report import generate_gate1_markdown_report


def test_generate_gate1_markdown_report():
    md_report = generate_gate1_markdown_report(days=20)
    assert "# Gate 1 Validation & Mode Promotion Report" in md_report
    assert "SHA256:" in md_report
    assert "Executive Summary:" in md_report
