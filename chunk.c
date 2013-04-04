#include <ogg/ogg.h>
#include <stdio.h>
#include <stdlib.h>
#include <theora/theoradec.h>
#include <theora/theoraenc.h>
#include <unistd.h>
#include <vorbis/codec.h>

void get_next_page(ogg_page *og, ogg_sync_state *oy)
{
	static const long buffer_size = 8192;
	char *buffer;
	size_t bytes;

	while (ogg_sync_pageout(oy, og) != 1) {
		buffer = ogg_sync_buffer(oy, buffer_size);
		if (!buffer) {
			fprintf(stderr, "Cannot allocate buffer.\n");
			exit(EXIT_FAILURE);
		}

		bytes = fread(buffer, 1, buffer_size, stdin);
		ogg_sync_wrote(oy, bytes);
	}
}

void put_page(ogg_page *og)
{
	fwrite(og->header, 1, og->header_len, stdout);
	fwrite(og->body, 1, og->body_len, stdout);
}

int main(int argc, char **argv)
{
	ogg_packet op;
	ogg_page og;
	ogg_stream_state *tmp;
	ogg_stream_state *ts = NULL;
	ogg_stream_state *vs = NULL;
	ogg_sync_state oy;
	th_info ti;
	th_comment tc;
	th_setup_info *tsi = NULL;

	(void)argc;
	(void)argv;

	ogg_sync_init(&oy);

	th_info_init(&ti);
	th_comment_init(&tc);

	for (get_next_page(&og, &oy); ogg_page_bos(&og); get_next_page(&og, &oy)) {
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
			put_page(&og);
			continue;
		}

		if (!ts && th_decode_headerin(&ti, &tc, &tsi, &op) > 0) {
			ts = tmp;
			put_page(&og);
			continue;
		}

		ogg_stream_destroy(tmp);
	}

	for (; ts || vs; get_next_page(&og, &oy)) {
		if (ts && ts->serialno == ogg_page_serialno(&og)) {
			put_page(&og);
			if (ogg_page_eos(&og)) {
				ogg_stream_destroy(ts);
				ts = NULL;
			}
		} else if (vs && vs->serialno == ogg_page_serialno(&og)) {
			put_page(&og);
			if (ogg_page_eos(&og)) {
				ogg_stream_destroy(vs);
				vs = NULL;
			}
		}
	}
	
	ogg_sync_clear(&oy);
	return EXIT_SUCCESS;
}
