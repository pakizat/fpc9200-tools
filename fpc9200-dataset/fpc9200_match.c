#include <math.h>
#include <stdint.h>
#include <stdlib.h>

#define IMAGE_WIDTH 112
#define IMAGE_HEIGHT 88
#define EDGE_MARGIN 8

typedef struct {
    int score;
    int raw_score;
    int center_score;
    int dx;
    int dy;
    int edge_score;
    int block_mean;
    int block_median;
    int block_min;
    int block_top4;
    int block_good_250;
    int block_good_350;
    int block_good_450;
} Fpc9200MatchResult;

static int
round_score(double value)
{
    return value >= 0.0 ? (int)(value + 0.5) : (int)(value - 0.5);
}

static int
cmp_int_desc(const void *a, const void *b)
{
    int ia = *(const int *) a;
    int ib = *(const int *) b;
    return (ib > ia) - (ib < ia);
}

static int
ncc_score_accum(double s_a,
                double s_b,
                double s_aa,
                double s_bb,
                double s_ab,
                int    nn)
{
    if (nn < 20)
        return 0;

    double cov = s_ab - (s_a * s_b) / nn;
    double var_a = s_aa - (s_a * s_a) / nn;
    double var_b = s_bb - (s_b * s_b) / nn;

    if (var_a <= 1.0 || var_b <= 1.0)
        return 0;

    return round_score((1000.0 * cov) / sqrt(var_a * var_b));
}

static void
compute_block_features(const uint8_t *templ,
                       const uint8_t *probe,
                       int            dx,
                       int            dy,
                       Fpc9200MatchResult *result)
{
    int scores[16] = {0};
    int count = 0;
    int sum = 0;

    for (int by = 0; by < 4; by++) {
        for (int bx = 0; bx < 4; bx++) {
            int x0 = 8 + bx * 24;
            int x1 = 8 + (bx + 1) * 24;
            int y0 = 8 + by * 18;
            int y1 = 8 + (by + 1) * 18;
            double s_a = 0.0, s_b = 0.0, s_aa = 0.0, s_bb = 0.0, s_ab = 0.0;
            int nn = 0;

            for (int y = y0; y < y1; y++) {
                int yy = y + dy;
                if (yy < EDGE_MARGIN || yy >= IMAGE_HEIGHT - EDGE_MARGIN)
                    continue;

                for (int x = x0; x < x1; x++) {
                    int xx = x + dx;
                    if (xx < EDGE_MARGIN || xx >= IMAGE_WIDTH - EDGE_MARGIN)
                        continue;

                    double a = templ[y * IMAGE_WIDTH + x];
                    double b = probe[yy * IMAGE_WIDTH + xx];
                    s_a += a;
                    s_b += b;
                    s_aa += a * a;
                    s_bb += b * b;
                    s_ab += a * b;
                    nn++;
                }
            }

            int score = ncc_score_accum(s_a, s_b, s_aa, s_bb, s_ab, nn);
            scores[count++] = score;
            sum += score;
        }
    }

    int sorted[16];
    for (int i = 0; i < 16; i++)
        sorted[i] = scores[i];
    qsort(sorted, 16, sizeof(int), cmp_int_desc);

    int good250 = 0, good350 = 0, good450 = 0;
    for (int i = 0; i < 16; i++) {
        if (scores[i] >= 250)
            good250++;
        if (scores[i] >= 350)
            good350++;
        if (scores[i] >= 450)
            good450++;
    }

    result->block_mean = round_score(sum / 16.0);
    result->block_median = round_score((sorted[7] + sorted[8]) / 2.0);
    result->block_min = sorted[15];
    result->block_top4 = round_score((sorted[0] + sorted[1] + sorted[2] + sorted[3]) / 4.0);
    result->block_good_250 = good250;
    result->block_good_350 = good350;
    result->block_good_450 = good450;
}

static int
gradient_mag(const uint8_t *image, int x, int y)
{
    int gx = image[y * IMAGE_WIDTH + (x + 1)] - image[y * IMAGE_WIDTH + (x - 1)];
    int gy = image[(y + 1) * IMAGE_WIDTH + x] - image[(y - 1) * IMAGE_WIDTH + x];
    return abs(gx) + abs(gy);
}

static int
compute_edge_score(const uint8_t *templ,
                   const uint8_t *probe,
                   int            dx,
                   int            dy)
{
    double s_a = 0.0, s_b = 0.0, s_aa = 0.0, s_bb = 0.0, s_ab = 0.0;
    int nn = 0;

    for (int y = EDGE_MARGIN + 1; y < IMAGE_HEIGHT - EDGE_MARGIN - 1; y++) {
        int yy = y + dy;
        if (yy < EDGE_MARGIN + 1 || yy >= IMAGE_HEIGHT - EDGE_MARGIN - 1)
            continue;

        for (int x = EDGE_MARGIN + 1; x < IMAGE_WIDTH - EDGE_MARGIN - 1; x++) {
            int xx = x + dx;
            if (xx < EDGE_MARGIN + 1 || xx >= IMAGE_WIDTH - EDGE_MARGIN - 1)
                continue;

            double a = gradient_mag(templ, x, y);
            double b = gradient_mag(probe, xx, yy);
            s_a += a;
            s_b += b;
            s_aa += a * a;
            s_bb += b * b;
            s_ab += a * b;
            nn++;
        }
    }

    return ncc_score_accum(s_a, s_b, s_aa, s_bb, s_ab, nn);
}

int
fpc9200_match_image(const uint8_t *templ,
                    const uint8_t *probe,
                    int search_radius,
                    int offset_penalty,
                    Fpc9200MatchResult *result)
{
    const int edge = EDGE_MARGIN;
    double best_score = -999999.0;
    double best_raw = -999999.0;
    int best_dx = 0;
    int best_dy = 0;

    if (!templ || !probe || !result)
        return -1;

    for (int dy = -search_radius; dy <= search_radius; dy++) {
        for (int dx = -search_radius; dx <= search_radius; dx++) {
            double s_a = 0.0, s_b = 0.0, s_aa = 0.0, s_bb = 0.0, s_ab = 0.0;
            int nn = 0;

            for (int y = edge; y < IMAGE_HEIGHT - edge; y++) {
                int yy = y + dy;
                if (yy < edge || yy >= IMAGE_HEIGHT - edge)
                    continue;

                for (int x = edge; x < IMAGE_WIDTH - edge; x++) {
                    int xx = x + dx;
                    if (xx < edge || xx >= IMAGE_WIDTH - edge)
                        continue;

                    double a = templ[y * IMAGE_WIDTH + x];
                    double b = probe[yy * IMAGE_WIDTH + xx];
                    s_a += a;
                    s_b += b;
                    s_aa += a * a;
                    s_bb += b * b;
                    s_ab += a * b;
                    nn++;
                }
            }

            if (nn < 100)
                continue;

            double cov = s_ab - (s_a * s_b) / nn;
            double var_a = s_aa - (s_a * s_a) / nn;
            double var_b = s_bb - (s_b * s_b) / nn;

            if (var_a <= 1.0 || var_b <= 1.0)
                continue;

            double raw = (1000.0 * cov) / sqrt(var_a * var_b);
            double score = raw - (abs(dx) + abs(dy)) * offset_penalty;

            if (score > best_score) {
                best_score = score;
                best_raw = raw;
                best_dx = dx;
                best_dy = dy;
            }
        }
    }

    int x0 = IMAGE_WIDTH / 4;
    int y0 = IMAGE_HEIGHT / 4;
    int x1 = (IMAGE_WIDTH * 3) / 4;
    int y1 = (IMAGE_HEIGHT * 3) / 4;
    double s_a = 0.0, s_b = 0.0, s_aa = 0.0, s_bb = 0.0, s_ab = 0.0;
    int nn = 0;

    for (int y = y0; y < y1; y++) {
        int yy = y + best_dy;
        if (yy < edge || yy >= IMAGE_HEIGHT - edge)
            continue;

        for (int x = x0; x < x1; x++) {
            int xx = x + best_dx;
            if (xx < edge || xx >= IMAGE_WIDTH - edge)
                continue;

            double a = templ[y * IMAGE_WIDTH + x];
            double b = probe[yy * IMAGE_WIDTH + xx];
            s_a += a;
            s_b += b;
            s_aa += a * a;
            s_bb += b * b;
            s_ab += a * b;
            nn++;
        }
    }

    int center_score = 0;
    if (nn >= 100) {
        double cov = s_ab - (s_a * s_b) / nn;
        double var_a = s_aa - (s_a * s_a) / nn;
        double var_b = s_bb - (s_b * s_b) / nn;
        if (var_a > 1.0 && var_b > 1.0)
            center_score = round_score((1000.0 * cov) / sqrt(var_a * var_b));
    }

    result->score = round_score(best_score > -999999.0 ? best_score : 0.0);
    result->raw_score = round_score(best_raw > -999999.0 ? best_raw : 0.0);
    result->center_score = center_score;
    result->dx = best_dx;
    result->dy = best_dy;
    result->edge_score = compute_edge_score(templ, probe, best_dx, best_dy);
    compute_block_features(templ, probe, best_dx, best_dy, result);
    return 0;
}
