=== Sitewell Bridge ===
Requires at least: 5.6
Requires PHP: 7.4
Stable tag: 0.1.0
License: MIT

Lets the sitewell SEO agent read and write SEO titles and descriptions and clear page
caches over the REST API.

== Install ==

1. In WordPress admin go to Plugins, Add New, Upload Plugin, and upload sitewell-bridge.zip.
2. Activate it. It adds no menu and no settings page.
3. Create a user for the agent with the Editor role (Users, Add New).
4. Open that user's profile, find Application Passwords, type a name such as "sitewell"
   and press Add. Copy the password once; WordPress never shows it again.

== Check it ==

Replace the site, login and password, then run:

  curl -u "LOGIN:APP PASSWORD" https://example.com/wp-json/sitewell/v1/ping

If that answers 401 although the password is right, the host is dropping the
Authorization header. Send the same pair in the plugin's own header instead:

  curl -H "X-Sitewell-Auth: $(printf '%s' 'LOGIN:APP PASSWORD' | base64)" https://example.com/wp-json/sitewell/v1/ping

A security plugin can switch application passwords off altogether (Wordfence has a
setting for it). The ping answer says whether they are available.

== Routes ==

GET  /wp-json/sitewell/v1/ping
GET  /wp-json/sitewell/v1/seo/<post id>
POST /wp-json/sitewell/v1/seo/<post id>     {"title": "...", "description": "...", "focus_keyword": "..."}
POST /wp-json/sitewell/v1/cache/purge       {"id": 12} for one post, {} for the whole site

Works with Yoast SEO and Rank Math. Known caches: WP Rocket, LiteSpeed Cache,
W3 Total Cache, WP Super Cache.
