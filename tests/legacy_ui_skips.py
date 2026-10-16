"""Tests der alten Jinja/HTMX-Oberfläche, die nur noch Altseiten-Inhalt per GET prüfen.

Seit `ui/cutover-redirects` antwortet jede GET-Seite der alten Oberfläche mit einer
Umleitung auf die Svelte-Oberfläche (`thermoctl/web/legacy_redirects.py`); die Seiten
selbst werden nie mehr ausgeliefert. Die Tests unten prüften deren Inhalt oder deren
Zugriffsschutz am GET und können deshalb nicht mehr gelten.

Grund der Übersprünge: `legacy-ui: wird im Löschschritt entfernt`. Diese Datei und die
Eintragungen in `conftest.py` (`pytest_collection_modifyitems`) verschwinden zusammen mit den
Tests, die sie nennt. Verfahren, nicht Dauerzustand.
"""

REASON = "legacy-ui: wird im Löschschritt entfernt"

# Dateiname (ohne Endung) -> Namen der Testfunktionen (ohne Parameter).
LEGACY_UI_TESTS: dict[str, frozenset[str]] = {
    "test_account_views": frozenset(
        {
            "test_a_tenant_reaches_their_own_account_page",
            "test_the_tenant_page_uses_the_tenant_shell",
        }
    ),
    "test_admin_views": frozenset(
        {
            "test_the_group_page_shows_permissions_by_area_in_plain_words",
            "test_the_rendered_group_form_changes_the_group_and_the_rights",
            "test_the_user_list_needs_user_manage",
        }
    ),
    "test_app": frozenset(
        {
            "test_the_login_page_includes_the_stylesheet",
        }
    ),
    "test_assets": frozenset(
        {
            "test_html_announces_asset_set_for_tabs_open_during_an_update",
        }
    ),
    "test_audit_views": frozenset(
        {
            "test_action_actually_filters",
            "test_audit_requires_audit_read",
            "test_detail_is_only_visible_on_request",
            "test_from_actually_filters",
            "test_object_filters_type_and_optional_id",
            "test_pagination_returns_the_second_page",
            "test_source_actually_filters",
            "test_the_date_filter_uses_the_configured_local_day_not_utc_midnight",
            "test_to_before_from_shows_a_message_and_keeps_the_values",
            "test_to_filters_inclusive_of_the_whole_day",
            "test_unreadable_filter_values_show_messages_instead_of_422",
            "test_user_actually_filters",
        }
    ),
    "test_changes_views": frozenset(
        {
            "test_changes_page_requires_audit_read",
            "test_changes_page_shows_before_and_after",
            "test_confirmation_and_success_restore_the_value",
            "test_conflict_needs_second_confirmation",
            "test_date_and_reverted_filters",
            "test_filters_and_escapes_free_text",
            "test_invalid_filter_choices_keep_the_form_usable",
            "test_page_two_and_bad_filters",
            "test_revert_needs_change_right_but_not_audit_read",
            "test_tenant_profile_cannot_open_change_log",
            "test_unknown_and_repeated_revert_are_friendly",
        }
    ),
    "test_control": frozenset(
        {
            "test_an_invalid_curve_point_rejects_the_whole_write_without_data_loss",
            "test_backup_offsets_are_only_shown_and_saved_with_device_manage",
            "test_operating_pages_describe_armed_after_restart_truthfully",
            "test_operating_pages_describe_armed_before_restart_truthfully",
            "test_operating_pages_describe_dry_run_truthfully",
            "test_saving_the_sensor_failure_defaults_round_trips",
            "test_the_operations_page_names_the_second_bolt",
            "test_the_page_shows_the_dry_run",
            "test_the_settings_page_shows_the_effective_sensor_failure_profile",
            "test_the_settings_page_shows_the_outdoor_source_selection",
            "test_the_solar_setback_can_be_switched_on_through_its_own_form",
            "test_the_start_page_keeps_the_three_latches_apart",
            "test_the_zone_parameter_page_shows_the_sensor_failure_section",
        }
    ),
    "test_control_solar": frozenset(
        {
            "test_the_page_shows_the_off_state_by_default",
        }
    ),
    "test_control_views": frozenset(
        {
            "test_an_unknown_period_falls_back_to_the_default",
            "test_the_arming_notes_name_both_command_paths_behind_the_second_latch",
            "test_the_operating_page_shows_notbetrieb_detail_and_comparison",
            "test_the_operating_page_shows_pi_and_its_fallback_reason",
            "test_the_operating_page_shows_the_most_recent_decision_per_zone",
        }
    ),
    "test_controller_channels": frozenset(
        {
            "test_a_fixed_number_with_more_than_one_decimal_survives_an_unchanged_resubmit",
            "test_a_room_sensor_used_only_as_temperature_source_appears_as_a_source",
            "test_a_saved_read_channel_keeps_its_kind_selected_after_reload",
            "test_a_saved_write_channel_shows_every_field_after_reload",
            "test_resubmitting_the_rendered_form_unchanged_keeps_the_configuration",
            "test_the_controller_page_and_both_form_endpoints",
            "test_the_controllers_page_shows_a_configured_channel",
            "test_the_controllers_page_with_no_visible_zone_shows_nothing",
            "test_the_edit_form_is_shown_only_to_a_principal_who_may_manage_the_zone",
        }
    ),
    "test_daily_views": frozenset(
        {
            "test_a_running_timed_override_describes_its_remaining_time_not_its_end_as_past",
            "test_an_enabled_but_now_ineligible_pi_switch_is_never_disabled_in_the_form",
            "test_an_ineligible_zone_shows_the_reason_and_refuses_to_switch_pi_on",
            "test_parameters_of_a_foreign_zone_yield_404",
            "test_pi_enabled_stays_on_across_further_saves_without_reconfirming",
            "test_showing_and_cancelling_an_override",
            "test_the_operating_mode_is_shown_only_when_it_deviates",
            "test_the_overview_explains_a_missing_reading_and_shows_the_decision",
            "test_the_overview_labels_heating_as_a_decision",
            "test_the_overview_shows_the_latest_of_several_decisions",
            "test_the_parameter_page_shows_inherited_values_and_empty_restores_inheritance",
            "test_the_parameter_page_shows_the_pi_warning_with_the_switching_table_and_relay_wear_link",
            "test_the_parameter_page_shows_the_window_temp_drop_switch_off_by_default",
            "test_the_pi_warning_ratio_follows_a_changed_assumption",
            "test_the_rendered_parameter_form_carries_the_valve_protection_field_names",
            "test_the_start_page_names_no_source_and_stale_distinctly_from_a_value",
            "test_the_start_page_shows_a_forgotten_window_chip",
            "test_the_start_page_shows_the_outdoor_temperature",
            "test_the_start_page_uses_local_day_and_time_for_its_schedule_track",
            "test_valve_protection_checkbox_round_trip",
            "test_without_setpoint_write_no_thermostat_is_on_the_page",
        }
    ),
    "test_daily_views_backup_offset": frozenset(
        {
            "test_a_stored_backup_offset_is_shown_in_its_input",
            "test_saving_without_touching_the_backup_offset_keeps_it",
        }
    ),
    "test_daily_views_solar": frozenset(
        {
            "test_the_page_shows_the_documented_default_of_zero",
            "test_the_setback_maximum_inherits_the_global_default",
        }
    ),
    "test_data_flow": frozenset(
        {
            "test_page_is_closed_to_tenants_and_anonymous_visitors",
            "test_page_lists_only_visible_zones",
            "test_page_shows_the_zone_with_diagram_source_and_edge_list",
            "test_page_without_the_right_is_empty_like_its_neighbours",
            "test_single_zone_page_has_one_heading_and_the_dry_run_note",
            "test_single_zone_page_refuses_a_foreign_zone",
        }
    ),
    "test_device_assignment": frozenset(
        {
            "test_a_thermostat_can_be_dragged_onto_the_actuator_slot",
            "test_drop_targets_only_with_device_manage",
            "test_the_page_shows_assignments_and_the_temperature_source",
            "test_the_page_shows_the_buttons_that_actually_arrived",
            "test_the_regulation_switch_is_rendered_with_its_own_id_and_a_fallback",
            "test_the_writing_routes_and_their_permissions",
            "test_zugeordnete_karten_tragen_ihre_kennung",
        }
    ),
    "test_device_commands_views": frozenset(
        {
            "test_absenkung_entries_are_shown_with_kind_text_and_labels",
            "test_absenkung_entries_need_audit_read_like_every_other_entry",
            "test_device_commands_requires_audit_read",
            "test_error_and_reason_are_shown",
            "test_from_actually_filters",
            "test_outcome_filter_actually_filters",
            "test_pagination_returns_the_second_page",
            "test_the_outcome_filter_hides_absenkung_entries",
            "test_the_page_does_not_call_the_value_before_the_setback_a_schedule",
            "test_the_rendered_filter_form_actually_filters",
            "test_the_time_shown_is_local_not_utc",
            "test_the_zone_filter_lists_a_zone_with_commands_and_absenkung_only_once",
            "test_the_zone_filter_offers_a_zone_that_only_has_absenkung_entries",
            "test_to_before_from_shows_a_message_and_keeps_the_values",
            "test_to_filters_inclusive_of_the_whole_day",
            "test_unreadable_filter_values_show_messages_instead_of_422",
            "test_zone_filter_matches_the_name_snapshot_of_a_deleted_zone",
        }
    ),
    "test_device_views": frozenset(
        {
            "test_a_meross_socket_never_reconciled_says_so_without_claiming_a_report",
            "test_a_meross_socket_recently_confirmed_online_is_not_flagged_as_never_reporting",
            "test_an_empty_device_page_explains_the_normal_case",
            "test_battery_and_radio_appear_as_a_number_not_as_a_chip",
            "test_the_device_page_needs_device_read",
            "test_the_device_page_puts_conspicuous_devices_on_top",
            "test_the_device_page_shows_signs_of_life_capability_and_zone",
            "test_the_start_page_shows_zone_state_without_a_zero_temperature",
        }
    ),
    "test_emergency_display_http": frozenset(
        {
            "test_kiosk_dashboard_shows_the_ersatzquelle_banner",
            "test_start_page_shows_the_ersatzquelle_banner",
            "test_tenant_page_shows_the_ersatzquelle_banner",
            "test_tenant_top_banner_shows_notbetrieb_instead_of_the_calm_message",
        }
    ),
    "test_glossary_views": frozenset(
        {
            "test_a_boosted_navigation_returns_the_whole_page",
            "test_a_help_icon_is_an_accessible_link_that_keeps_unsaved_form_input",
            "test_a_signed_in_user_without_any_permission_can_read_the_glossary",
            "test_every_entry_carries_the_text_the_search_looks_in",
            "test_every_see_also_link_on_the_page_hits_an_entry_on_the_page",
            "test_help_icons_carry_the_ingress_prefix",
            "test_the_current_page_is_marked_in_the_navigation",
            "test_the_glossary_is_alphabetical_in_the_page",
            "test_the_glossary_link_in_the_navigation_is_there_even_without_any_permission",
            "test_the_glossary_lists_every_entry_with_its_texts",
            "test_the_jump_marks_cover_a_to_z_and_link_only_to_letters_that_exist",
            "test_the_operation_page_offers_help_for_the_dry_run",
            "test_the_plant_view_links_to_the_glossary_in_navigation_and_footer",
            "test_the_search_field_exists_but_stays_hidden_until_the_script_runs",
            "test_the_settings_page_offers_help_for_the_emergency_profile_and_the_curve",
            "test_the_tenant_help_page_points_to_the_glossary",
            "test_the_tenant_view_gets_the_glossary_in_its_own_shell",
            "test_the_zone_parameter_page_offers_help_for_its_hard_terms",
        }
    ),
    "test_homebridge_interface": frozenset(
        {
            "test_broker_placeholder_hint_when_mqtt_host_is_not_configured",
            "test_global_zone_read_shows_every_zone",
            "test_no_zone_visible_without_zone_read",
            "test_only_the_zone_read_scoped_zone_is_shown",
            "test_password_field_is_a_placeholder_not_thermoctls_own",
            "test_the_page_names_the_zone_and_carries_placeholder_credentials",
            "test_the_rendered_topics_match_publication_py",
        }
    ),
    "test_ingress_prefix": frozenset(
        {
            "test_htmx_and_form_attributes_carry_the_prefix",
            "test_rendered_page_carries_the_prefix_on_every_local_link",
            "test_rendered_page_direct_uses_bare_links_even_with_ingress_configured",
            "test_rendered_page_with_forged_header_uses_bare_links",
            "test_rendered_page_without_prefix_uses_bare_local_links",
        }
    ),
    "test_kiosk": frozenset(
        {
            "test_a_kiosk_cookie_reaches_no_administration_page",
            "test_a_previously_chosen_ansicht_survives_a_visit_without_the_query_string",
            "test_a_setpoint_beyond_the_domain_limit_shows_up_as_an_error",
            "test_an_explicit_ansicht_is_reflected_and_written_to_its_own_cookie",
            "test_an_ordinary_api_token_does_not_work_as_a_kiosk_cookie",
            "test_an_unknown_ansicht_value_falls_back_to_auto_instead_of_failing",
            "test_ansicht_defaults_to_auto_with_neither_query_nor_cookie",
            "test_boosting_a_zone_without_a_schedule_shows_up_as_an_error",
            "test_the_buttons_on_the_dashboard_work_the_way_a_browser_sends_them",
            "test_the_cancel_button_appears_once_a_boost_is_running",
            "test_the_dashboard_carries_an_agpl_source_link_that_stays_off_the_tablet",
            "test_the_dashboard_shows_a_shadow_decision_once_one_exists",
            "test_the_dashboard_shows_only_the_assigned_zone",
            "test_the_dashboard_without_any_cookie_shows_the_invalid_page",
            "test_the_entry_link_sets_a_cookie_and_redirects_without_the_token_in_the_url",
            "test_the_kiosk_keeps_its_own_fixed_refresh",
            "test_the_rendered_kiosk_clock_uses_the_configured_timezone",
            "test_the_rendered_kiosk_token_expiry_uses_the_configured_timezone",
            "test_the_ui_profile_of_the_issuer_does_not_reach_the_kiosk",
        }
    ),
    "test_mapping_map": frozenset(
        {
            "test_page_answers_lists_connections_and_is_visible_zone_filtered",
            "test_page_marks_technical_ports_and_stored_zone_ports",
            "test_page_needs_a_right_and_is_closed_to_tenants_and_anonymous",
        }
    ),
    "test_mapping_map_multi": frozenset(
        {
            "test_a_one_sensor_is_the_temperature_source_of_two_zones_and_both_zones_read_it",
            "test_f_device_to_device_fan_out_is_judged_fit_but_never_stored",
        }
    ),
    "test_mapping_map_valve": frozenset(
        {
            "test_page_carries_the_legend_and_the_tooltip",
        }
    ),
    "test_mode_views": frozenset(
        {
            "test_a_builtin_mode_cannot_be_deleted_and_says_why",
            "test_a_mode_in_use_cannot_be_deleted",
            "test_fremde_zone_ergibt_404",
            "test_modusliste_braucht_mode_manage",
            "test_the_delete_form_for_an_unused_mode",
            "test_the_edit_mode_form_shows_the_values",
            "test_the_frost_protection_mode_cannot_be_deleted_and_says_why",
            "test_the_new_mode_form_is_shown",
            "test_the_setpoint_form_shows_every_mode_and_the_help_text",
            "test_unbekannter_modus_ergibt_404",
        }
    ),
    "test_navigation": frozenset(
        {
            "test_no_section_heading_remains_when_none_of_its_entries_is_available",
            "test_user_with_all_permissions_sees_every_navigation_item",
            "test_user_without_user_management_neither_sees_link_nor_gets_access",
            "test_zone_scoped_permission_shows_only_zone_filtering_destinations",
        }
    ),
    "test_notification_settings": frozenset(
        {
            "test_a_row_without_the_columns_yet_still_renders_with_the_documented_default",
            "test_never_shown_when_notify_last_attempt_at_is_absent",
            "test_the_settings_page_shows_the_three_notification_toggles",
            "test_the_settings_page_shows_the_window_alarm_toggle",
            "test_without_a_webhook_the_button_is_not_offered",
        }
    ),
    "test_passkey": frozenset(
        {
            "test_the_login_page_offers_passkeys_only_when_set_up",
            "test_the_passkey_page_explains_a_missing_setup",
        }
    ),
    "test_relay_wear_views": frozenset(
        {
            "test_relay_wear_empty_period_is_explicit",
            "test_relay_wear_highlights_actionable_levels_and_the_visible_assumption",
            "test_relay_wear_reflects_a_changed_assumption",
            "test_relay_wear_requires_audit_read_and_a_visible_zone",
        }
    ),
    "test_schedule_views": frozenset(
        {
            "test_an_unknown_schedule_point_yields_404",
            "test_carried_segments_are_visibly_marked_as_not_draggable",
            "test_copy_controls_are_natively_operable_without_javascript",
            "test_paint_copy_and_undo_routes_use_normal_csrf_protected_forms",
            "test_paint_mode_keeps_draggable_bars_movable_and_paints_everywhere_else",
            "test_painting_until_midnight_is_selectable_without_javascript",
            "test_permissions_and_foreign_zones_yield_404",
            "test_rendered_palette_defaults_to_painting_and_explains_move_only_gestures",
            "test_the_forecast_is_visible_with_only_zone_read",
            "test_the_forecast_shows_a_fixed_temperature_override_with_no_mode_name",
            "test_the_forecast_shows_the_running_bar_and_its_temperature",
            "test_the_rendered_mode_form_changes_the_point_without_javascript",
            "test_the_rendered_paint_form_works_without_javascript",
            "test_the_week_view_carries_the_point_identifier_for_dragging",
            "test_the_week_view_shows_the_wraparound_from_sunday_to_monday",
        }
    ),
    "test_security_review_2026_09_02": frozenset(
        {
            "test_bediengeraeteseite_verlangt_geraeteleserecht",
            "test_bediengeraeteseite_verraet_keine_fremden_geraetenamen",
        }
    ),
    "test_smoke_test": frozenset(
        {
            "test_boosted_navigation_returns_the_full_page",
            "test_the_new_zone_form_posts_to_a_real_endpoint",
            "test_the_rendered_form_carries_the_field_names_the_view_reads",
            "test_the_thermostat_buttons_send_values_their_view_accepts",
        }
    ),
    "test_tenant_history": frozenset(
        {
            "test_a_foreign_room_answers_exactly_like_one_that_does_not_exist",
            "test_a_global_and_a_room_grant_together_show_each_room_once",
            "test_a_global_read_right_shows_every_room_and_that_is_intended",
            "test_a_global_right_for_something_else_adds_no_room",
            "test_a_page_of_a_plant_that_is_not_armed_says_that_it_shows_decisions",
            "test_a_period_window_changes_the_tile",
            "test_a_right_for_a_different_permission_does_not_open_the_tile",
            "test_a_tenant_without_any_room_gets_an_empty_page_not_an_error",
            "test_an_administrator_does_not_reach_the_tenant_history",
            "test_an_unknown_period_is_refused_like_in_the_plant_view",
            "test_an_unparseable_room_id_is_not_found_like_everywhere_else_in_the_tenant_view",
            "test_each_tenant_sees_tiles_of_exactly_their_own_rooms",
            "test_every_address_carries_the_ingress_prefix",
            "test_rooms_of_two_access_groups_add_up_and_nothing_more",
            "test_the_chosen_period_is_marked_and_handed_to_every_tile",
            "test_the_heating_time_page_points_to_the_history",
            "test_the_history_page_is_a_page_of_the_tenant_view",
            "test_the_hover_data_of_a_tile_belongs_to_its_own_room_only",
            "test_the_notice_follows_the_same_source_as_the_heating_time_page",
            "test_the_page_explains_why_it_can_stay_cooler_than_the_plan",
            "test_the_plant_history_routes_stay_closed_to_tenants",
            "test_the_plant_navigation_does_not_list_the_tenant_history",
            "test_the_plant_tile_keeps_its_technical_wording",
            "test_the_room_id_must_be_plain_ascii_digits_and_nothing_else",
            "test_the_tenant_navigation_offers_the_history_next_to_the_heating_time",
            "test_the_tile_drawing_and_summary_carry_only_the_own_measurements",
            "test_the_tile_of_a_foreign_room_is_not_found_even_when_asked_for_directly",
            "test_the_tile_speaks_the_language_of_the_dwelling",
        }
    ),
    "test_tenant_views": frozenset(
        {
            "test_a_read_only_room_says_so_instead_of_showing_nothing",
            "test_a_room_taken_away_afterwards_no_longer_appears_in_the_absence_banner",
            "test_a_room_with_fewer_than_two_setpoints_says_what_is_missing",
            "test_a_room_without_a_state_row_is_skipped_by_the_notice",
            "test_a_room_without_any_switch_time_can_be_set_up_by_the_tenant",
            "test_a_running_absence_is_visible_and_can_be_ended_in_one_step",
            "test_a_running_override_is_shown_and_can_be_ended",
            "test_a_stale_measurement_is_explained_as_an_effect_not_a_cause",
            "test_a_tenant_without_any_room_gets_a_readable_page_not_an_error",
            "test_an_administrator_does_not_reach_the_tenant_pages",
            "test_an_open_window_is_reported_as_the_effect_it_has",
            "test_an_unknown_period_falls_back_instead_of_failing",
            "test_an_unparseable_zone_id_is_not_found_either",
            "test_every_link_carries_the_ingress_prefix",
            "test_every_period_renders_only_visible_rooms",
            "test_no_page_ever_mentions_a_foreign_room",
            "test_the_administrator_start_page_stays_the_plant_view",
            "test_the_adoption_appears_even_when_seen_from_a_read_only_room",
            "test_the_dry_run_is_spelled_out_as_text",
            "test_the_edit_control_is_a_control_not_a_line_of_text",
            "test_the_forecast_matches_the_domain",
            "test_the_live_poll_is_quiet_and_uses_the_server_interval",
            "test_the_next_switch_line_comes_from_the_domain",
            "test_the_schedule_lands_on_a_room_the_tenant_may_actually_edit",
            "test_the_schedule_of_a_foreign_room_cannot_be_opened",
            "test_the_tenant_shell_carries_the_stale_page_handling_and_the_loading_bar",
        }
    ),
    "test_ui_profile": frozenset(
        {
            "test_a_tenant_cannot_open_an_admin_page_even_with_the_permission",
            "test_the_group_page_offers_the_interface_choice",
            "test_the_guard_does_not_replace_the_permission_check",
            "test_the_profile_cannot_be_switched_from_the_client",
        }
    ),
    "test_vacation_views": frozenset(
        {
            "test_cancelling_ends_the_running_vacation",
            "test_the_form_is_hidden_without_the_permission",
            "test_the_form_is_shown_with_the_permission",
            "test_the_page_needs_zone_read",
            "test_the_page_shows_no_vacation_by_default",
        }
    ),
    "test_web_oauth_consent": frozenset(
        {
            "test_login_returns_to_consent",
        }
    ),
    "test_web_tokens_presets": frozenset(
        {
            "test_preset_selects_owned_permissions",
            "test_token_list_shows_oauth_source_and_client",
            "test_zone_scoped_permission_can_be_selected_without_widening",
        }
    ),
    "test_zone_history_chart": frozenset(
        {
            "test_every_layout_draws_inside_its_own_view_box",
            "test_invalid_periods_are_rejected_without_a_server_error",
            "test_page_places_the_chart_above_the_form_with_accessible_summary",
            "test_partial_route_and_zone_isolation",
            "test_the_chart_escapes_the_zone_name",
        }
    ),
    "test_zone_history_honesty": frozenset(
        {
            "test_a_changing_setback_is_labelled_with_its_range_not_its_maximum",
            "test_a_setback_shorter_than_a_pixel_gets_a_marker_but_no_stretched_band",
            "test_a_ten_minute_setback_in_seven_days_has_a_thin_band_and_a_marker",
            "test_compressed_windows_show_the_range_of_each_section_and_say_so",
            "test_drawn_widths_carry_no_minimum_that_would_claim_time",
            "test_legacy_rows_are_marked_unknown_and_never_reported_as_no_setback",
            "test_the_curve_legend_says_up_to_and_not_a_fixed_length",
            "test_the_legend_of_a_mixed_window_has_no_statement_of_none",
            "test_the_legend_of_the_24_hour_window_names_three_minutes",
            "test_the_legend_of_the_heat_lane_names_a_share_per_section_not_a_duration",
            "test_the_legend_shows_the_last_measurement_with_its_time_not_a_bucket_mean",
            "test_the_spike_bar_reaches_every_variant_of_the_page",
            "test_the_stamp_reaches_the_legend_of_the_page",
        }
    ),
    "test_zone_history_hover": frozenset(
        {
            "test_a_card_carries_parseable_hover_data_and_the_drawing_sizes",
            "test_a_card_without_data_has_no_hover_data",
            "test_every_drawing_carries_its_own_plot_measures",
            "test_hatch_patterns_get_one_id_per_tile",
            "test_navigation_and_the_zone_list_point_to_the_overview",
            "test_overview_answers_like_the_zone_list_without_the_right",
            "test_overview_is_a_page_of_its_own_and_not_mistaken_for_a_zone",
            "test_overview_is_closed_to_tenants_and_anonymous_visitors",
            "test_overview_lists_only_the_zones_each_user_may_manage",
            "test_overview_rejects_an_unknown_period_and_passes_the_valid_one_to_every_tile",
            "test_overview_tile_of_a_foreign_zone_is_refused_even_when_asked_for_directly",
            "test_the_card_on_the_zone_page_keeps_its_ids_and_the_tile_gets_its_own",
        }
    ),
    "test_zone_views": frozenset(
        {
            "test_a_zone_permission_for_another_zone_yields_404",
            "test_editing_a_zone_shows_the_values_and_saves_the_change",
            "test_the_delete_confirmation_names_every_dependency",
            "test_the_empty_zone_form_needs_an_installation_wide_permission",
            "test_the_zone_list_shows_only_visible_zones",
        }
    ),
}
