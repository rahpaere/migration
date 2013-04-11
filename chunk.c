#include <ogg/ogg.h>
#include <stdio.h>
#include <stdlib.h>
#include <theora/theoradec.h>
#include <theora/theoraenc.h>
#include <unistd.h>
#include <vorbis/codec.h>

struct output {
	FILE *file;
	FILE *bosfile;
	char *path;
	unsigned count;
};

void usage(const char *program)
{
	fprintf(stderr, "Usage: %s [-q QUALITY] [-p PACKETS_PER_CHUNK] SERIALNO\n", program);
	exit(EXIT_FAILURE);
}

void get_options(int *quality, int *packets_per_chunk, int argc, char **argv)
{
	*quality = 0;
	*packets_per_chunk = 0;

	for (;;)
		switch (getopt(argc, argv, "q:p:?")) {
		case 'q':
			*quality = atoi(optarg);
			break;
		case 'p':
			*packets_per_chunk = atoi(optarg);
			break;
		case -1:
			return;
		default:
			usage(argv[0]);
		}
}

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

void next_output(struct output *out)
{
	out->count++;
	open_output(out);
}

void setup_output(struct output *out)
{
	out->file = NULL;
	out->path = NULL;
	out->count = 0;
	open_output(out);
	out->bosfile = out->file;
	out->file = NULL;
	next_output(out);
}

void teardown_output(struct output *out)
{
	fclose(out->bosfile);
	fclose(out->file);
	free(out->path);
}

void put_page(ogg_page *og, struct output *out)
{
	FILE *f = ogg_page_bos(og) ? out->bosfile : out->file;

	fwrite(og->header, 1, og->header_len, f);
	fwrite(og->body, 1, og->body_len, f);
	fflush(f);
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

int get_next_page_in_stream(ogg_page *og, int serialno, struct output *out, ogg_sync_state *oy)
{
	for (;;) {
		if (get_next_page(og, oy) != 0)
			return -1;
		else if (ogg_page_serialno(og) == serialno)
			return 0;
		else
			put_page(og, out);
	}
}

int get_next_packet(ogg_packet *op, ogg_stream_state *os, struct output *out, ogg_sync_state *oy)
{
	ogg_page og;

	while (ogg_stream_packetout(os, op) != 1) {
		if (get_next_page_in_stream(&og, os->serialno, out, oy) < 0)
			return -1;
		ogg_stream_pagein(os, &og);
	};

	return 0;
}

int get_stream(ogg_stream_state *os, int serialno, struct output *out, ogg_sync_state *oy)
{
	ogg_page og;

	if (get_next_page_in_stream(&og, serialno, out, oy) < 0)
		return -1;

	ogg_stream_pagein(os, &og);
	return 0;
}

int get_first_video_packet(ogg_packet *op, th_info *ti, th_comment *tc, th_setup_info **tsi, ogg_stream_state *os, struct output *out, ogg_sync_state *oy)
{
	int status;

	th_info_init(ti);
	th_comment_init(tc);
	*tsi = NULL;

	for (;;) {
		if (get_next_packet(op, os, out, oy) != 0)
			return -1;

		status = th_decode_headerin(ti, tc, tsi, op);
		if (status == 0)
			break;
		else if (status > 0)
			continue;

		fprintf(stderr, "Error decoding theora header.\n");
		exit(EXIT_FAILURE);
	}

	return 0;
}

void put_pages(ogg_stream_state *os, struct output *out)
{
	ogg_page og;

	while (ogg_stream_flush(os, &og))
		put_page(&og, out);
}

int main(int argc, char **argv)
{
	int serialno;
	int quality;
	ogg_int64_t granpos;
	ogg_uint32_t keyframe_frequency;
	ogg_packet op;
	ogg_packet oop;
	ogg_stream_state ts;
	ogg_stream_state ots;
	ogg_sync_state oy;
	int status;
	struct output out;
	th_comment tc;
	th_info ti;
	th_setup_info *tsi;
	th_dec_ctx *td;
	th_enc_ctx *te;
	th_ycbcr_buffer ycbcr;
	int packets;
	int packets_per_chunk;

	get_options(&quality, &packets_per_chunk, argc, argv);
	if (argc - optind != 1)
		usage(argv[0]);
	serialno = atoi(argv[optind]);

	setup_output(&out);

	ogg_sync_init(&oy);
	ogg_stream_init(&ts, serialno);

	get_first_video_packet(&op, &ti, &tc, &tsi, &ts, &out, &oy);

	td = th_decode_alloc(&ti, tsi);
	if (!td) {
		fprintf(stderr, "Error allocating theora decoder.\n");
		exit(EXIT_FAILURE);
	}

	if (quality)
		ti.quality = quality;
	if (!packets_per_chunk)
		packets_per_chunk = ti.fps_numerator * ti.fps_denominator / 2;

	te = th_encode_alloc(&ti);
	if (!te) {
		fprintf(stderr, "Error allocating theora encoder.\n");
		exit(EXIT_FAILURE);
	}

	th_setup_free(tsi);

	ogg_stream_init(&ots, ts.serialno);
	for (;;) {
		status = th_encode_flushheader(te, &tc, &oop);
		if (status < 0) {
			fprintf(stderr, "Error flushing theora headers.\n");
			exit(EXIT_FAILURE);
		} else if (status == 0) {
			break;
		}
		ogg_stream_packetin(&ots, &oop);
		put_pages(&ots, &out);
	}

	packets = 0;
	do {
		status = th_decode_packetin(td, &op, &granpos);
		if (status != 0 && status != TH_DUPFRAME) {
			fprintf(stderr, "Error decoding theora packet.\n");
			exit(EXIT_FAILURE);
		}
		th_decode_ycbcr_out(td, ycbcr);

		if (!packets) {
			keyframe_frequency = 1;
			th_encode_ctl(te, TH_ENCCTL_SET_KEYFRAME_FREQUENCY_FORCE, &keyframe_frequency, sizeof(keyframe_frequency));
		}

		th_encode_ycbcr_in(te, ycbcr);
		while (th_encode_packetout(te, ogg_stream_eos(&ts), &oop) > 0) {
			ogg_stream_packetin(&ots, &oop);
			put_pages(&ots, &out);
		}

		if (!packets) {
			keyframe_frequency = 1 << ti.keyframe_granule_shift;
			th_encode_ctl(te, TH_ENCCTL_SET_KEYFRAME_FREQUENCY_FORCE, &keyframe_frequency, sizeof(keyframe_frequency));
		}

		packets = (packets + 1) % packets_per_chunk;
		if (!packets)
			next_output(&out);
	} while (get_next_packet(&op, &ts, &out, &oy) == 0);

	/* XXX why does this segfault near the end? */

	th_decode_free(td);
	th_encode_free(te);
	ogg_stream_destroy(&ts);
	ogg_stream_destroy(&ots);
	ogg_sync_clear(&oy);
	teardown_output(&out);
	return EXIT_SUCCESS;
}
