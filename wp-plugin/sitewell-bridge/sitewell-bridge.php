<?php
/**
 * Plugin Name:       Sitewell Bridge
 * Plugin URI:        https://github.com/Amirjaz/sitewell
 * Description:       Lets the sitewell SEO agent read and write SEO titles and descriptions and clear page caches over the REST API, signed in with a WordPress application password.
 * Version:           0.1.0
 * Requires at least: 5.6
 * Requires PHP:      7.4
 * Author:            Amirjaz
 * License:           MIT
 *
 * Routes, all under /wp-json/sitewell/v1 and all behind a signed-in user:
 *
 *   GET  /ping          who am I, which SEO plugin, are application passwords on
 *   GET  /seo/<id>      the SEO title, description and focus keyword of a post
 *   POST /seo/<id>      set any of them; an empty string resets a field to the plugin's default
 *   POST /cache/purge   clear caches for one post ({"id": 12}) or the whole site
 *
 * Sign-in is a normal application password. Some hosts drop the Authorization header
 * before PHP sees it, so the same "login:password" pair, base64-encoded, is also accepted
 * in X-Sitewell-Auth. Both are refused over plain http.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'SITEWELL_BRIDGE_VERSION', '0.1.0' );
define( 'SITEWELL_BRIDGE_NAMESPACE', 'sitewell/v1' );

/* ------------------------------------------------------------------ sign-in --- */

/**
 * True when this request is for the REST API. REST_REQUEST is not defined yet when
 * another plugin asks for the current user early, so the URL is checked instead.
 */
function sitewell_bridge_is_rest_url() {
	if ( isset( $_GET['rest_route'] ) ) { // phpcs:ignore WordPress.Security.NonceVerification
		return true;
	}
	$uri = isset( $_SERVER['REQUEST_URI'] ) ? (string) $_SERVER['REQUEST_URI'] : '';
	return false !== strpos( $uri, '/' . rest_get_url_prefix() . '/' );
}

/**
 * Accept an application password from X-Sitewell-Auth when nothing else signed the
 * request in. Runs after WordPress's own check (priority 20), so it never overrides it.
 *
 * @param int|false $user_id The user found so far.
 * @return int|false
 */
function sitewell_bridge_header_auth( $user_id ) {
	if ( $user_id || empty( $_SERVER['HTTP_X_SITEWELL_AUTH'] ) ) {
		return $user_id;
	}
	if ( ! is_ssl() || ! sitewell_bridge_is_rest_url() || ! function_exists( 'wp_authenticate_application_password' ) ) {
		return $user_id;
	}

	$pair = base64_decode( (string) $_SERVER['HTTP_X_SITEWELL_AUTH'], true ); // phpcs:ignore WordPress.PHP.DiscouragedPHPFunctions
	if ( false === $pair || false === strpos( $pair, ':' ) ) {
		return $user_id;
	}
	list( $login, $password ) = explode( ':', $pair, 2 );

	add_filter( 'application_password_is_api_request', '__return_true' );
	$user = wp_authenticate_application_password( null, $login, $password );
	remove_filter( 'application_password_is_api_request', '__return_true' );

	return ( $user instanceof WP_User ) ? $user->ID : $user_id;
}
add_filter( 'determine_current_user', 'sitewell_bridge_header_auth', 21 );

/* -------------------------------------------------------------- SEO plugins --- */

/** Which SEO plugin stores the fields: 'yoast', 'rankmath' or 'none'. */
function sitewell_bridge_seo_plugin() {
	if ( defined( 'WPSEO_VERSION' ) ) {
		return 'yoast';
	}
	if ( defined( 'RANK_MATH_VERSION' ) ) {
		return 'rankmath';
	}
	return 'none';
}

/** The post meta key behind each field, for the active SEO plugin. */
function sitewell_bridge_meta_keys() {
	switch ( sitewell_bridge_seo_plugin() ) {
		case 'yoast':
			return array(
				'title'         => '_yoast_wpseo_title',
				'description'   => '_yoast_wpseo_metadesc',
				'focus_keyword' => '_yoast_wpseo_focuskw',
			);
		case 'rankmath':
			return array(
				'title'         => 'rank_math_title',
				'description'   => 'rank_math_description',
				'focus_keyword' => 'rank_math_focus_keyword',
			);
	}
	return array();
}

/** The stored SEO fields of a post, as the REST response. */
function sitewell_bridge_seo_fields( $post_id ) {
	$fields = array(
		'id'        => (int) $post_id,
		'plugin'    => sitewell_bridge_seo_plugin(),
		'permalink' => get_permalink( $post_id ),
		'status'    => get_post_status( $post_id ),
		'modified'  => get_post_modified_time( 'c', true, $post_id ),
	);
	foreach ( sitewell_bridge_meta_keys() as $field => $key ) {
		$fields[ $field ] = (string) get_post_meta( $post_id, $key, true );
	}
	return $fields;
}

/* ------------------------------------------------------------------- routes --- */

function sitewell_bridge_can_edit() {
	return current_user_can( 'edit_others_posts' );
}

function sitewell_bridge_can_edit_post( WP_REST_Request $request ) {
	$post_id = (int) $request['id'];
	return get_post( $post_id ) && current_user_can( 'edit_post', $post_id );
}

function sitewell_bridge_ping() {
	$user = wp_get_current_user();
	return array(
		'bridge'                => SITEWELL_BRIDGE_VERSION,
		'wordpress'             => get_bloginfo( 'version' ),
		'php'                   => PHP_VERSION,
		'seo_plugin'            => sitewell_bridge_seo_plugin(),
		'application_passwords' => function_exists( 'wp_is_application_passwords_available' ) && wp_is_application_passwords_available(),
		'user'                  => $user->user_login,
		'can_publish'           => current_user_can( 'publish_posts' ),
		'signed_in_with'        => empty( $_SERVER['HTTP_X_SITEWELL_AUTH'] ) ? 'authorization' : 'x-sitewell-auth',
	);
}

function sitewell_bridge_get_seo( WP_REST_Request $request ) {
	return sitewell_bridge_seo_fields( (int) $request['id'] );
}

function sitewell_bridge_set_seo( WP_REST_Request $request ) {
	$post_id = (int) $request['id'];
	$keys    = sitewell_bridge_meta_keys();
	if ( ! $keys ) {
		return new WP_Error( 'sitewell_no_seo_plugin', 'Neither Yoast SEO nor Rank Math is active.', array( 'status' => 409 ) );
	}

	$changed = array();
	foreach ( $keys as $field => $key ) {
		if ( ! $request->has_param( $field ) ) {
			continue;
		}
		$value = sanitize_text_field( (string) $request->get_param( $field ) );
		if ( '' === $value ) {
			delete_post_meta( $post_id, $key );
		} else {
			update_post_meta( $post_id, $key, $value );
		}
		$changed[] = $field;
	}

	if ( $changed ) {
		clean_post_cache( $post_id );
		/** Fires after the agent changed SEO fields, so a site can hook its own cache clearing. */
		do_action( 'sitewell_bridge_seo_updated', $post_id, $changed );
	}

	$fields            = sitewell_bridge_seo_fields( $post_id );
	$fields['changed'] = $changed;
	return $fields;
}

function sitewell_bridge_purge( WP_REST_Request $request ) {
	$post_id = (int) $request->get_param( 'id' );
	$purged  = array();

	if ( $post_id ) {
		clean_post_cache( $post_id );
	} else {
		wp_cache_flush();
	}
	$purged[] = 'object-cache';

	if ( $post_id && function_exists( 'rocket_clean_post' ) ) {
		rocket_clean_post( $post_id );
		$purged[] = 'wp-rocket';
	} elseif ( ! $post_id && function_exists( 'rocket_clean_domain' ) ) {
		rocket_clean_domain();
		$purged[] = 'wp-rocket';
	}

	if ( defined( 'LSCWP_V' ) ) {
		if ( $post_id ) {
			do_action( 'litespeed_purge_post', $post_id );
		} else {
			do_action( 'litespeed_purge_all' );
		}
		$purged[] = 'litespeed';
	}

	if ( $post_id && function_exists( 'w3tc_flush_post' ) ) {
		w3tc_flush_post( $post_id );
		$purged[] = 'w3-total-cache';
	} elseif ( ! $post_id && function_exists( 'w3tc_flush_all' ) ) {
		w3tc_flush_all();
		$purged[] = 'w3-total-cache';
	}

	if ( $post_id && function_exists( 'wp_cache_post_change' ) ) {
		wp_cache_post_change( $post_id );
		$purged[] = 'wp-super-cache';
	} elseif ( ! $post_id && function_exists( 'wp_cache_clear_cache' ) ) {
		wp_cache_clear_cache();
		$purged[] = 'wp-super-cache';
	}

	/** Fires after the built-in purges, so a site can clear a cache this plugin does not know. */
	do_action( 'sitewell_bridge_purged', $post_id );

	return array(
		'id'     => $post_id ? $post_id : null,
		'purged' => $purged,
	);
}

function sitewell_bridge_register_routes() {
	$text = array(
		'type'              => 'string',
		'sanitize_callback' => 'sanitize_text_field',
	);

	register_rest_route(
		SITEWELL_BRIDGE_NAMESPACE,
		'/ping',
		array(
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'sitewell_bridge_ping',
			'permission_callback' => 'sitewell_bridge_can_edit',
		)
	);

	register_rest_route(
		SITEWELL_BRIDGE_NAMESPACE,
		'/seo/(?P<id>\d+)',
		array(
			array(
				'methods'             => WP_REST_Server::READABLE,
				'callback'            => 'sitewell_bridge_get_seo',
				'permission_callback' => 'sitewell_bridge_can_edit_post',
			),
			array(
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => 'sitewell_bridge_set_seo',
				'permission_callback' => 'sitewell_bridge_can_edit_post',
				'args'                => array(
					'title'         => $text,
					'description'   => $text,
					'focus_keyword' => $text,
				),
			),
		)
	);

	register_rest_route(
		SITEWELL_BRIDGE_NAMESPACE,
		'/cache/purge',
		array(
			'methods'             => WP_REST_Server::CREATABLE,
			'callback'            => 'sitewell_bridge_purge',
			'permission_callback' => 'sitewell_bridge_can_edit',
			'args'                => array(
				'id' => array(
					'type'    => 'integer',
					'minimum' => 1,
				),
			),
		)
	);
}
add_action( 'rest_api_init', 'sitewell_bridge_register_routes' );
