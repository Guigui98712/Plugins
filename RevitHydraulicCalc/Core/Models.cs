using System;
using System.Collections.Generic;

namespace RevitHydraulicCalc.Core
{
    /// <summary>
    /// Representa um segmento de tubulação (trecho entre dois nós)
    /// </summary>
    public class PipeSegment
    {
        public string Id { get; set; } = string.Empty;
        public string Name { get; set; } = string.Empty;
        public double DiameterMm { get; set; } // mm
        public double DiameterM => DiameterMm / 1000.0;
        public double LengthM { get; set; } // m
        public double RoughnessMm { get; set; } = 0.046; // mm (aço galvanizado padrão)
        public double HazenWilliamsC { get; set; } = 120; // coeficiente C
        public double StartElevationM { get; set; }
        public double EndElevationM { get; set; }
        public double FlowRateLps { get; set; } // L/s
        public double FlowRateM3s => FlowRateLps / 1000.0;
        public string Material { get; set; } = "Aço Galvanizado";
        public List<Fitting> Fittings { get; set; } = new();
        public string StartNodeId { get; set; } = string.Empty;
        public string EndNodeId { get; set; } = string.Empty;
        public int ElementId { get; set; }
    }

    /// <summary>
    /// Representa uma conexão/acessório (perda localizada)
    /// </summary>
    public class Fitting
    {
        public string Type { get; set; } = string.Empty; // Cotovelo, Tê, Válvula, etc.
        public double EquivalentLengthM { get; set; } // comprimento equivalente em m
        public double KFactor { get; set; } // fator K alternativo
        public int Quantity { get; set; } = 1;
    }

    /// <summary>
    /// Nó da rede hidráulica
    /// </summary>
    public class HydraulicNode
    {
        public string Id { get; set; } = string.Empty;
        public string Name { get; set; } = string.Empty;
        public double ElevationM { get; set; }
        public double PressureKPa { get; set; } // pressão disponível
        public double PressureMca => PressureKPa / 9.81; // metros de coluna d'água
        public bool IsCritical { get; set; }
        public bool IsOutlet { get; set; } // ponto de saída (folha)
        public bool IsSource { get; set; } // ponto de origem
        public double MinRequiredPressureKPa { get; set; } = 100; // mínimo padrão
        public List<string> ConnectedSegmentIds { get; set; } = new();
        public int? RevitElementId { get; set; }
        public string SystemName { get; set; } = string.Empty;
    }

    /// <summary>
    /// Resultado do cálculo por trecho
    /// </summary>
    public class SegmentResult
    {
        public string SegmentId { get; set; } = string.Empty;
        public string SegmentName { get; set; } = string.Empty;
        public double DistributedLossKPa { get; set; }
        public double LocalizedLossKPa { get; set; }
        public double ElevationLossKPa { get; set; }
        public double TotalLossKPa { get; set; }
        public double VelocityMs { get; set; }
        public double StartPressureKPa { get; set; }
        public double EndPressureKPa { get; set; }
        public double FlowRateLps { get; set; }
        public double DiameterMm { get; set; }
        public double LengthM { get; set; }
        public string StartNodeName { get; set; } = string.Empty;
        public string EndNodeName { get; set; } = string.Empty;
    }

    /// <summary>
    /// Resultado completo da análise
    /// </summary>
    public class HydraulicAnalysisResult
    {
        public string SystemName { get; set; } = string.Empty;
        public DateTime AnalysisDate { get; set; } = DateTime.Now;
        public List<SegmentResult> SegmentResults { get; set; } = new();
        public List<HydraulicNode> Nodes { get; set; } = new();
        public List<string> CriticalWarnings { get; set; } = new();
        public double SourcePressureKPa { get; set; }
    }
}
