import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.List;
import org.noise_planet.noisemodelling.propagation.AttenuationParameters;
import org.noise_planet.noisemodelling.propagation.cnossos.AttenuationCnossos;
import org.noise_planet.noisemodelling.propagation.cnossos.CnossosPath;
import org.noise_planet.noisemodelling.propagation.cnossos.SegmentPath;

/** Actual installed/overlay Java replay for H/F plus fixed saved ray calls. */
public final class AgroundHFReplay {
  public static void main(String[] args) throws Exception {
    List<Integer> bands = new ArrayList<>(List.of(63, 125, 250, 500, 1000, 2000, 4000, 8000));
    AttenuationParameters data = new AttenuationParameters();
    data.setFrequencies(bands);
    double defaultCelerity = data.getCelerity();
    CnossosPath path = new CnossosPath();
    try (BufferedReader reader = new BufferedReader(new InputStreamReader(System.in))) {
      String line;
      while ((line = reader.readLine()) != null) {
        if (line.isBlank()) continue;
        String[] f = line.split("\\t", -1);
        if (f.length != 14) throw new IllegalArgumentException("expected 14 TSV fields, got " + f.length);
        String key = f[0];
        String method = f[1];
        int fi = Integer.parseInt(f[2]);
        boolean favourable = Boolean.parseBoolean(f[3]);
        boolean force = Boolean.parseBoolean(f[4]);
        double celerity = f[13].equals("-") ? defaultCelerity : Double.parseDouble(f[13]);
        data.celerity = celerity;
        path.setFavourable(favourable);
        SegmentPath segment = new SegmentPath();
        segment.dp = Double.parseDouble(f[5]);
        segment.zsH = Double.parseDouble(f[6]);
        segment.zrH = Double.parseDouble(f[7]);
        segment.zsF = Double.parseDouble(f[8]);
        segment.zrF = Double.parseDouble(f[9]);
        segment.gPath = Double.parseDouble(f[10]);
        segment.gPathPrime = Double.parseDouble(f[11]);
        segment.testFormH = Double.parseDouble(f[12]);
        double result;
        if (method.equals("H")) result = AttenuationCnossos.aGroundH(path, segment, data, fi, force);
        else if (method.equals("F")) result = AttenuationCnossos.aGroundF(path, segment, data, fi, force);
        else throw new IllegalArgumentException("method must be H or F: " + method);
        System.out.println(key + "\t" + Double.toString(result));
      }
    }
  }
}
