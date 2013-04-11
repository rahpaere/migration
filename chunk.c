#include <ogg/ogg.h>
#include <stdio.h>
#include <stdlib.h>
#include <theora/theoradec.h>
#include <theora/theoraenc.h>
#include <unistd.h>
#include <vorbis/codec.h>

struct output {
	FILE *file;
	char *path;
	unsigned count;
};

void open_output(struct output *out)
{
	static const char *format = "output%u.ogv";
	int size;

	if (out->file)
		fclose(out->file);
	if (out->path)
		free(out->path);

	size = snprintf(NULL, 0, format, out->count);
	out->path = malloc(size + 1);
	if (!out->path) {
		perror("malloc");
		exit(EXIT_FAILURE);
	}
	snprintf(out->path, size + 1, format, out->count);

	out->file = fopen(out->path, "w");
	if (!out->file) {
		fprintf(stderr, "Unable to open file %u.\n", out->count);
		exit(EXIT_FAILURE);
	}
}

void setup_output(struct output *out)
{
	out->file = NULL;
	out->path = NULL;
	out->count = 0;
	open_output(out);
}

void next_output(struct output *out)
{
	out->count++;
	open_output(out);
}

void teardown_output(struct output *out)
{
	fclose(out->file);
	free(out->path);
}

int get_next_page(ogg_page *og, ogg_sync_state *oy)
{
	static const long buffer_size = 8192;
	char *buffer;
	size_t bytes;

	while (ogg_sync_pageout(oy, og) != 1) {
		if (feof(stdin) || ferror(stdin))
			return -1;

		buffer = ogg_sync_buffer(oy, buffer_size);
		if (!buffer) {
			fprintf(stderr, "Cannot allocate buffer.\n");
			return -1;
		}

		bytes = fread(buffer, 1, buffer_size, stdin);
		ogg_sync_wrote(oy, bytes);
	}

	return 0;
}

void put_page(ogg_page *og, struct output *out)
{
	fwrite(og->header, 1, og->header_len, out->file);
	fwrite(og->body, 1, og->body_len, out->file);
}

int main(int argc, char **argv)
{
	ogg_packet op;
	ogg_page og;
	ogg_stream_state *tmp;
	ogg_stream_state *ts = NULL;
	ogg_stream_state *vs = NULL;
	ogg_sync_state oy;
	struct output out;
	th_comment tc;
	th_info ti;
	th_setup_info *tsi = NULL;
	unsigned pages;

	(void)argc;
	(void)argv;

	ogg_sync_init(&oy);

	th_info_init(&ti);
	th_comment_init(&tc);

	setup_output(&out);

	while (get_next_page(&og, &oy) == 0 && ogg_page_bos(&og)) {
		tmp = malloc(sizeof(*tmp));
		if (!tmp) {
			perror("malloc");
			exit(EXIT_FAILURE);
		}

		ogg_stream_init(tmp, ogg_page_serialno(&og));
		ogg_stream_pagein(tmp, &og);
		ogg_stream_packetout(tmp, &op);

		if (!vs && vorbis_synthesis_idheader(&op)) {
			vs = tmp;
			put_page(&og, &out);
			continue;
		}

		if (!ts && th_decode_headerin(&ti, &tc, &tsi, &op) > 0) {
			ts = tmp;
			put_page(&og, &out);
			continue;
		}

		ogg_stream_destroy(tmp);
	}

	next_output(&out);
	pages = 0;

	do {
		if (ts && ts->serialno == ogg_page_serialno(&og)) {
			put_page(&og, &out);
			pages++;
			if (pages > 100) {
				next_output(&out);
				pages = 0;
			}
		} else if (vs && vs->serialno == ogg_page_serialno(&og)) {
			put_page(&og, &out);
		}
	} while (get_next_page(&og, &oy) == 0);
	
	if (ts)
		ogg_stream_destroy(ts);
	if (vs)
		ogg_stream_destroy(vs);
	ogg_sync_clear(&oy);
	teardown_output(&out);
	return EXIT_SUCCESS;
}
