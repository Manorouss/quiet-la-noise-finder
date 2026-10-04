import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Locale;
import org.noise_planet.noisemodelling.emission.road.cnossos.RoadCnossos;
import org.noise_planet.noisemodelling.emission.road.cnossos.RoadCnossosParameters;

/**
 * Reads tab-separated traffic rows on stdin and prints CNOSSOS-EU road emission
 * (dB/m, unweighted) per octave band using the pinned NoiseModelling emission jar.
 *
 * Input columns: key LV MV HGV WAV WBV LV_SPD MV_SPD HGV_SPD WAV_SPD WBV_SPD
 * PVMT TS_STUD PM_STUD JUNC_DIST JUNC_TYPE WAY TEMPERATURE
 * Output columns: key LW63 LW125 LW250 LW500 LW1000 LW2000 LW4000 LW8000
 */
public class EmissionDump {
    private static final int[] BANDS = {63, 125, 250, 500, 1000, 2000, 4000, 8000};

    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        StringBuilder out = new StringBuilder();
        String line;
        while ((line = in.readLine()) != null) {
            if (line.isBlank()) continue;
            String[] f = line.split("\t");
            double[] q = new double[5];
            double[] v = new double[5];
            for (int i = 0; i < 5; i++) {
                q[i] = Double.parseDouble(f[1 + i]);
                v[i] = Double.parseDouble(f[6 + i]);
            }
            String surface = f[11];
            double tsStud = Double.parseDouble(f[12]);
            double pmStud = Double.parseDouble(f[13]);
            double juncDist = Double.parseDouble(f[14]);
            int juncType = (int) Double.parseDouble(f[15]);
            int way = (int) Double.parseDouble(f[16]);
            double temperature = Double.parseDouble(f[17]);
            out.append(f[0]);
            for (int band : BANDS) {
                RoadCnossosParameters p = new RoadCnossosParameters(v[0], v[1], v[2], v[3], v[4],
                        q[0], q[1], q[2], q[3], q[4], band, temperature, surface, tsStud, pmStud, juncDist, juncType);
                p.setWay(way);
                double lw = RoadCnossos.evaluate(p);
                out.append('\t').append(String.format(Locale.ROOT, "%.6f", lw));
            }
            out.append('\n');
        }
        System.out.print(out);
    }
}
