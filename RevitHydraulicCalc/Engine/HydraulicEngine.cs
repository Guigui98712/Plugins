using RevitHydraulicCalc.Core;
using System;
using System.Collections.Generic;
using System.Linq;

namespace RevitHydraulicCalc.Engine
{
    /// <summary>
    /// Motor de cálculo hidráulico. Independente do Revit — permite testes unitários.
    /// </summary>
    public static class HydraulicEngine
    {
        public const double GRAVITY = 9.81; // m/s²
        public const double WATER_DENSITY = 1000; // kg/m³
        public const double WATER_VISCOSITY = 0.000001004; // m²/s a 20°C

        /// <summary>
        /// Calcula perda de carga distribuída pelo método de Hazen-Williams.
        /// J = 10.67 * Q^1.852 / (C^1.852 * D^4.87)  [m/m]
        /// </summary>
        public static double CalculateHazenWilliamsLoss(double flowRateM3s, double diameterM, double lengthM, double cFactor)
        {
            if (flowRateM3s <= 0 || diameterM <= 0 || lengthM <= 0 || cFactor <= 0)
                return 0;

            // Hazen-Williams em unidades métricas
            double j = 10.67 * Math.Pow(flowRateM3s, 1.852) 
                       / (Math.Pow(cFactor, 1.852) * Math.Pow(diameterM, 4.87));

            return j * lengthM; // perda em metros
        }

        /// <summary>
        /// Converte perda em metros para kPa
        /// </summary>
        public static double MetersToKPa(double meters) => meters * WATER_DENSITY * GRAVITY / 1000.0;

        /// <summary>
        /// Converte kPa para metros de coluna d'água
        /// </summary>
        public static double KPaToMca(double kPa) => kPa / (WATER_DENSITY * GRAVITY / 1000.0);

        /// <summary>
        /// Calcula velocidade do fluido no tubo
        /// </summary>
        public static double CalculateVelocity(double flowRateM3s, double diameterM)
        {
            if (diameterM <= 0) return 0;
            double area = Math.PI * Math.Pow(diameterM, 2) / 4.0;
            return flowRateM3s / area;
        }

        /// <summary>
        /// Calcula perda localizada por comprimento equivalente
        /// </summary>
        public static double CalculateLocalizedLossByEquivalentLength(
            List<Fitting> fittings, double flowRateM3s, double diameterM, double cFactor)
        {
            double totalEqLength = fittings.Sum(f => f.EquivalentLengthM * f.Quantity);
            if (totalEqLength <= 0) return 0;
            return CalculateHazenWilliamsLoss(flowRateM3s, diameterM, totalEqLength, cFactor);
        }

        /// <summary>
        /// Retorna coeficiente C do Hazen-Williams por material
        /// </summary>
        public static double GetHazenWilliamsC(string material)
        {
            return material?.ToLower() switch
            {
                "pvc" or "pvcu" or "pe" or "ppr" => 150,
                "cobre" or "cobre recozido" => 140,
                "aço galvanizado" or "aco galvanizado" => 120,
                "aço carbono" or "aco carbono" => 110,
                "ferro fundido" => 100,
                "concreto" => 110,
                _ => 120 // padrão: aço galvanizado
            };
        }

        /// <summary>
        /// Retorna rugosidade absoluta em mm por material
        /// </summary>
        public static double GetRoughnessMm(string material)
        {
            return material?.ToLower() switch
            {
                "pvc" or "pvcu" or "pe" or "ppr" => 0.0015,
                "cobre" or "cobre recozido" => 0.0015,
                "aço galvanizado" or "aco galvanizado" => 0.046,
                "aço carbono" or "aco carbono" => 0.046,
                "ferro fundido" => 0.26,
                "concreto" => 0.3,
                _ => 0.046
            };
        }

        /// <summary>
        /// Tabela de comprimento equivalente para acessórios (valores típicos em m)
        /// Baseado em Macintyre - Instalações Hidráulicas Prediais
        /// </summary>
        public static double GetEquivalentLength(string fittingType, double diameterMm)
        {
            var type = fittingType?.ToLower().Trim() ?? "";

            // Tabela simplificada - valores em metros
            return type switch
            {
                "cotovelo 90°" or "cotovelo 90" or "curva 90" => diameterMm <= 25 ? 0.6 : diameterMm <= 50 ? 1.2 : diameterMm <= 100 ? 2.1 : 3.4,
                "cotovelo 45°" or "cotovelo 45" or "curva 45" => diameterMm <= 25 ? 0.3 : diameterMm <= 50 ? 0.6 : diameterMm <= 100 ? 1.0 : 1.7,
                "tê direto" or "te direto" or "tee direto" => diameterMm <= 25 ? 0.3 : diameterMm <= 50 ? 0.6 : diameterMm <= 100 ? 1.0 : 1.7,
                "tê derivação" or "te derivacao" or "tee derivacao" => diameterMm <= 25 ? 0.9 : diameterMm <= 50 ? 1.8 : diameterMm <= 100 ? 3.1 : 5.2,
                "válvula de gaveta" or "valvula gaveta" or "registro gaveta" => diameterMm <= 25 ? 0.2 : diameterMm <= 50 ? 0.4 : diameterMm <= 100 ? 0.7 : 1.1,
                "válvula de retenção" or "valvula retencao" or "retenção" => diameterMm <= 25 ? 1.5 : diameterMm <= 50 ? 3.0 : diameterMm <= 100 ? 5.2 : 8.6,
                "válvula de globo" or "valvula globo" => diameterMm <= 25 ? 4.0 : diameterMm <= 50 ? 8.0 : diameterMm <= 100 ? 14.0 : 23.0,
                "redução" or "reducao" => diameterMm <= 25 ? 0.2 : diameterMm <= 50 ? 0.4 : diameterMm <= 100 ? 0.7 : 1.1,
                "ampliação" or "ampliacao" => diameterMm <= 25 ? 0.1 : diameterMm <= 50 ? 0.2 : diameterMm <= 100 ? 0.4 : 0.6,
                _ => 0.5 // valor conservador genérico
            };
        }

        /// <summary>
        /// Calcula pressão disponível em todos os nós a partir da origem
        /// </summary>
        public static HydraulicAnalysisResult AnalyzeNetwork(
            List<PipeSegment> segments,
            List<HydraulicNode> nodes,
            string sourceNodeId,
            double sourcePressureKPa)
        {
            var result = new HydraulicAnalysisResult
            {
                SourcePressureKPa = sourcePressureKPa,
                Nodes = nodes.Select(n => new HydraulicNode
                {
                    Id = n.Id,
                    Name = n.Name,
                    ElevationM = n.ElevationM,
                    IsOutlet = n.IsOutlet,
                    IsSource = n.IsSource,
                    MinRequiredPressureKPa = n.MinRequiredPressureKPa,
                    RevitElementId = n.RevitElementId,
                    SystemName = n.SystemName,
                    ConnectedSegmentIds = new List<string>(n.ConnectedSegmentIds)
                }).ToList()
            };

            // Inicializa pressões
            var nodePressures = result.Nodes.ToDictionary(n => n.Id, n => 0.0);
            nodePressures[sourceNodeId] = sourcePressureKPa;
            var sourceNode = result.Nodes.First(n => n.Id == sourceNodeId);
            sourceNode.PressureKPa = sourcePressureKPa;
            sourceNode.IsSource = true;

            // Constrói grafo de adjacência
            var adjacency = new Dictionary<string, List<(string segmentId, string toNodeId)>>();
            foreach (var seg in segments)
            {
                if (!adjacency.ContainsKey(seg.StartNodeId))
                    adjacency[seg.StartNodeId] = new List<(string, string)>();
                adjacency[seg.StartNodeId].Add((seg.Id, seg.EndNodeId));
            }

            // BFS/DFS a partir da origem
            var visited = new HashSet<string> { sourceNodeId };
            var queue = new Queue<string>();
            queue.Enqueue(sourceNodeId);

            while (queue.Count > 0)
            {
                var currentNodeId = queue.Dequeue();
                var currentPressure = nodePressures[currentNodeId];
                var currentNode = result.Nodes.FirstOrDefault(n => n.Id == currentNodeId);

                if (!adjacency.ContainsKey(currentNodeId)) continue;

                foreach (var (segmentId, toNodeId) in adjacency[currentNodeId])
                {
                    if (visited.Contains(toNodeId)) continue;

                    var segment = segments.FirstOrDefault(s => s.Id == segmentId);
                    if (segment == null) continue;

                    // Cálculo das perdas
                    double cFactor = GetHazenWilliamsC(segment.Material);
                    double distributedLossM = CalculateHazenWilliamsLoss(
                        segment.FlowRateM3s, segment.DiameterM, segment.LengthM, cFactor);

                    double localizedLossM = CalculateLocalizedLossByEquivalentLength(
                        segment.Fittings, segment.FlowRateM3s, segment.DiameterM, cFactor);

                    // Perda por elevação (desnível) - se sobe, perde pressão
                    double elevationLossM = segment.EndElevationM - segment.StartElevationM;

                    double totalLossM = distributedLossM + localizedLossM + elevationLossM;
                    double totalLossKPa = MetersToKPa(totalLossM);

                    double endPressure = currentPressure - totalLossKPa;
                    nodePressures[toNodeId] = endPressure;

                    var endNode = result.Nodes.FirstOrDefault(n => n.Id == toNodeId);
                    if (endNode != null)
                    {
                        endNode.PressureKPa = endPressure;
                        endNode.IsCritical = endNode.IsOutlet && endPressure < endNode.MinRequiredPressureKPa;
                    }

                    // Registra resultado do trecho
                    result.SegmentResults.Add(new SegmentResult
                    {
                        SegmentId = segment.Id,
                        SegmentName = segment.Name,
                        DistributedLossKPa = MetersToKPa(distributedLossM),
                        LocalizedLossKPa = MetersToKPa(localizedLossM),
                        ElevationLossKPa = MetersToKPa(elevationLossM),
                        TotalLossKPa = totalLossKPa,
                        VelocityMs = CalculateVelocity(segment.FlowRateM3s, segment.DiameterM),
                        StartPressureKPa = currentPressure,
                        EndPressureKPa = endPressure,
                        FlowRateLps = segment.FlowRateLps,
                        DiameterMm = segment.DiameterMm,
                        LengthM = segment.LengthM,
                        StartNodeName = currentNode?.Name ?? currentNodeId,
                        EndNodeName = endNode?.Name ?? toNodeId
                    });

                    if (endNode != null && endNode.IsCritical)
                    {
                        result.CriticalWarnings.Add(
                            $"Ponto crítico: {endNode.Name} — Pressão: {endNode.PressureKPa:F2} kPa " +
                            $"(mínimo: {endNode.MinRequiredPressureKPa:F2} kPa)");
                    }

                    visited.Add(toNodeId);
                    queue.Enqueue(toNodeId);
                }
            }

            // Verifica nós não alcançados
            foreach (var node in result.Nodes)
            {
                if (!visited.Contains(node.Id) && !node.IsSource)
                {
                    result.CriticalWarnings.Add($"Nó não alcançado: {node.Name} ({node.Id})");
                }
            }

            return result;
        }
    }
}
