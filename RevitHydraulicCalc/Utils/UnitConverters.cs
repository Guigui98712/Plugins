using System;

namespace RevitHydraulicCalc.Utils
{
    /// <summary>
    /// Conversores de unidades entre sistema imperial (Revit) e métrico
    /// </summary>
    public static class UnitConverters
    {
        // Comprimento
        public static double FeetToMeters(double feet) => feet * 0.3048;
        public static double MetersToFeet(double meters) => meters / 0.3048;
        public static double InchesToMm(double inches) => inches * 25.4;
        public static double MmToInches(double mm) => mm / 25.4;

        // Área
        public static double SqFeetToSqMeters(double sqFeet) => sqFeet * 0.092903;

        // Volume
        public static double CubicFeetToCubicMeters(double cf) => cf * 0.0283168;
        public static double CubicFeetToLiters(double cf) => cf * 28.3168;
        public static double LitersToCubicFeet(double liters) => liters / 28.3168;

        // Vazão
        public static double CfsToLps(double cfs) => cfs * 28.3168;
        public static double LpsToCfs(double lps) => lps / 28.3168;
        public static double CfsToM3s(double cfs) => cfs * 0.0283168;

        // Pressão
        public static double PsiToKPa(double psi) => psi * 6.89476;
        public static double KPaToPsi(double kPa) => kPa / 6.89476;
        public static double McaToKPa(double mca) => mca * 9.80665;
        public static double KPaToMca(double kPa) => kPa / 9.80665;

        // Velocidade
        public static double FpsToMps(double fps) => fps * 0.3048;
        public static double MpsToFps(double mps) => mps / 0.3048;

        /// <summary>
        /// Formata pressão em kPa com unidade
        /// </summary>
        public static string FormatPressure(double kPa)
        {
            double mca = KPaToMca(kPa);
            return $"{kPa:F2} kPa ({mca:F2} mca)";
        }

        /// <summary>
        /// Formata vazão em L/s com unidade
        /// </summary>
        public static string FormatFlow(double lps)
        {
            double m3h = lps * 3.6;
            return $"{lps:F3} L/s ({m3h:F2} m³/h)";
        }
    }
}
